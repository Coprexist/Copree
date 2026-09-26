"""触发组合规则 —— 条件组合 + 动作 + 作用域，一个引擎三种来源。

规则形状（插件声明、平台内置、AI 自写的决策技能共用）：

    {"id": "...",
     "when": {"event": "tool_result", "conditions": <条件树，见 utils/pure/conditions.py>},
     "do": {"action": "deliver", "text": "..."},
     "scope": "frame"}

scope（投几次）：
- **frame（默认）**：当前状态帧内投一次。compact/clear 后帧重建 → 会再投一次。
  这是"每次上下文重建讲一遍"的语义，不是"这个会话只讲一次"。
- always：每次命中都投。
- session / agent / version：状态层还没实现，写了当场拒绝——不假装支持。

信任边界：平台内置 > 插件 > AI。平台已认领的扩展名插件覆盖不了；AI 写的规则只能使用
SAFE_ACTIONS（写不了会吞掉结果之类的动作）。扩展名必须带命名空间（vendor.xxx）。

可观测：explain() 给出逐条规则的判定结果与原因，别让"不命中"变成黑盒。
"""
from __future__ import annotations

import logging

from app.utils.pure.conditions import PLATFORM, match_conditions, validate_conditions

logger = logging.getLogger(__name__)

TOOL_EVENTS = ("tool_call", "tool_result")
ENGINE_ACTIONS = ("deliver", "silent")
# AI 自写的规则只能用这些动作：deliver 只是多说一句，silent 会吞掉结果，不给
SAFE_ACTIONS = ("deliver",)
SCOPES = ("frame", "always")
DEFAULT_SCOPE = "frame"

_BUILTIN: list[dict] = []
_PLUGIN: list[dict] = []
_ACTIONS: dict[str, object] = {}


def register_action(name: str, fn, *, source: str = "plugin") -> None:
    """登记一个动作。fn(ctx, do, result) 返回要放进 result["_trigger"][name] 的内容。

    结果只落在 _trigger 命名空间下：自定义动作覆盖不了工具自己的字段（success / url / 结果本体）。
    """
    from app.utils.pure.conditions import _claim, _ns_name   # 与条件扩展共用命名与认领规则

    key = _ns_name(name, source, "动作")
    if key in ENGINE_ACTIONS:
        raise ValueError(f"动作名「{key}」是内置名，换个带命名空间的名字")
    if _claim(key, source, "动作"):
        _ACTIONS[key] = fn


def action_names() -> tuple[str, ...]:
    return ENGINE_ACTIONS + tuple(_ACTIONS)


def action_handler(name: str):
    return _ACTIONS.get(str(name or ""))


def register_builtin(rules) -> None:
    _extend(_BUILTIN, rules, source=PLATFORM)


def register_plugin(rules, *, source: str = "plugin") -> None:
    """登记插件声明的规则（ToolPlugin.triggers 走这里）。"""
    _extend(_PLUGIN, rules, source=source)


def _extend(target: list[dict], rules, *, source: str) -> None:
    for rule in rules or []:
        if not isinstance(rule, dict):
            continue
        rule_id = rule_id_of(rule)
        if any(rule_id_of(r) == rule_id for r in target):
            continue
        # 注册期不查"运算/判词是否已注册"：插件可能在声明规则之后才注册扩展，
        # 严格判定会把它误杀。引用不存在时求值按不命中处理，并留 debug 日志。
        ok, err = validate_trigger(rule, source=source, check_refs=False)
        if not ok:
            logger.warning("触发规则无效，已跳过：%s（%s）", rule_id, err)
            continue
        target.append(rule)


def rule_id_of(rule: dict) -> str:
    rid = str((rule or {}).get("id") or "").strip()
    if rid:
        return rid
    when = (rule or {}).get("when") or {}
    do = (rule or {}).get("do") or {}
    return "|".join([
        str(when.get("event") or ""),
        str(do.get("action") or ""),
        str(do.get("text") or do.get("reply") or "")[:80],
    ])


def builtin_rules() -> list[dict]:
    return list(_BUILTIN)


def plugin_rules() -> list[dict]:
    return list(_PLUGIN)


def agent_rules(rules) -> list[dict]:
    """AI 自写的规则只做信任降级校验，通过后与内置/插件同样参与求值。"""
    out: list[dict] = []
    for rule in rules or []:
        ok, err = validate_trigger(rule, source="ai")
        if ok:
            out.append(rule)
        else:
            logger.warning("AI 写的触发规则无效，已跳过：%s", err)
    return out


def all_rules(extra=()) -> list[dict]:
    return [*agent_rules(extra), *_BUILTIN, *_PLUGIN]


def _tool_values(node: dict) -> set[str]:
    """从条件树里抠出"盯着哪个工具"：规范写法与历史写法都认。"""
    found: set[str] = set()
    if "op" in node and str(node.get("field") or "") == "tool":
        value = node.get("value")
        found.update([value] if isinstance(value, str) else [v for v in (value or []) if isinstance(v, str)])
    for key, value in node.items():
        if key == "tool" and isinstance(value, str):
            found.add(value)
    return found


def targets_of(rule: dict) -> set[str] | None:
    """规则盯着哪些工具；None = 不挑工具（任何工具都要过一遍）。

    只为省查询：没有规则盯着的工具，调用时连状态都不用读。
    """
    conditions = ((rule or {}).get("when") or {}).get("conditions")
    if not conditions:
        return None
    found: set[str] = set()
    stack = [conditions]
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            continue
        found |= _tool_values(node)
        for key in ("and", "or"):
            if key in node and isinstance(node[key], list):
                stack.extend(node[key])
        if "not" in node:
            stack.append(node["not"])
    return found or None


def validate_trigger(rule: dict, *, source: str = "plugin", check_refs: bool = True) -> tuple[bool, str]:
    """校验一条规则；写错当场拒绝并说清可选值，不留给运行期安静地不触发。"""
    if not isinstance(rule, dict):
        return False, "规则必须是对象"
    when = rule.get("when")
    if not isinstance(when, dict) or not when:
        return False, "when 必填（事件 + 条件树）"
    event = str(when.get("event") or "").strip()
    if event and event not in TOOL_EVENTS:
        return False, f"when.event 只能是 {list(TOOL_EVENTS)}（其余事件归决策技能的情景表）"
    conditions = when.get("conditions")
    if conditions is not None:
        ok, err = validate_conditions(conditions, check_refs=check_refs)
        if not ok:
            return False, err
    do = rule.get("do") or {}
    action = str(do.get("action") or "")
    if not action:
        return False, "do.action 必填"
    if action == "deliver" and not str(do.get("text") or "").strip():
        return False, "deliver 需要 text（要投给 AI 的那句话）"
    if action not in action_names():
        return False, f"do.action 只能是 {list(action_names())} 之一，或先用 register_action 注册"
    if source == "ai" and action not in SAFE_ACTIONS:
        return False, f"AI 写的规则只能用 {list(SAFE_ACTIONS)}（{action} 会影响工具结果）"
    scope = str(rule.get("scope") or DEFAULT_SCOPE)
    if scope not in SCOPES:
        return False, f"scope 只能是 {list(SCOPES)}（session / agent / version 待状态层实现）"
    return True, ""


def scope_of(rule: dict) -> str:
    scope = str((rule or {}).get("scope") or DEFAULT_SCOPE)
    return scope if scope in SCOPES else DEFAULT_SCOPE


def hits(rules, ctx: dict, *, event: str = "tool_result") -> list[dict]:
    out: list[dict] = []
    for rule in rules or []:
        when = (rule or {}).get("when") or {}
        if str(when.get("event") or event) != event:
            continue
        conditions = when.get("conditions")
        if conditions is None or match_conditions(conditions, ctx):
            out.append(rule)
    return out


def explain(rules, ctx: dict, *, event: str = "tool_result", delivered=None) -> list[dict]:
    """逐条给出判定结果与原因（dry-run / 排查用）。

    原因取值：event_mismatch / conditions_false / already_delivered / matched
    """
    seen = set(delivered or ())
    out: list[dict] = []
    for rule in rules or []:
        rid = rule_id_of(rule)
        when = (rule or {}).get("when") or {}
        if str(when.get("event") or event) != event:
            out.append({"id": rid, "matched": False, "why": "event_mismatch"})
            continue
        conditions = when.get("conditions")
        if conditions is not None and not match_conditions(conditions, ctx):
            out.append({"id": rid, "matched": False, "why": "conditions_false"})
            continue
        if scope_of(rule) == "frame" and rid in seen:
            out.append({"id": rid, "matched": False, "why": "already_delivered"})
            continue
        out.append({"id": rid, "matched": True, "why": "matched"})
    return out
