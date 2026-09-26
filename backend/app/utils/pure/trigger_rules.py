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
# AI 自写的规则只能用这些内置动作。deliver 只是多说一句；silent 会**吞掉工具结果**，
# 等于让规则改写"AI 看到的世界"——只有随发布走的代码（平台/插件）能这么干，
# 否则 AI 可以把自己的工具结果藏起来逃避纠错。
SAFE_ACTIONS = ("deliver",)
SCOPES = ("frame", "always")
DEFAULT_SCOPE = "frame"

_BUILTIN: list[dict] = []
_PLUGIN: list[dict] = []
_ACTIONS: dict[str, object] = {}
_AI_ALLOWED: set[str] = set()


def register_action(name: str, fn, *, source: str = "plugin", ai_allowed: bool = False) -> None:
    """登记一个动作。fn(ctx, do, result) 返回要放进 result["_trigger"][name] 的内容。

    结果只落在 _trigger 命名空间下：自定义动作覆盖不了工具自己的字段（success / url / 结果本体）。
    注意 _trigger 是**对模型可见**的（它就是工具结果的一部分）——别往里面塞机器内部状态。

    ai_allowed（默认 False）是**逐项评审开关**，不是批量配置：开着它等于把"改 AI 所见"的轻量版
    交给 AI 自己用。开之前逐个想清楚这个动作最坏能干什么，别为了省事给整类动作批量打开。
    """
    from app.utils.pure.conditions import _claim, _ns_name   # 与条件扩展共用命名与认领规则

    key = _ns_name(name, source, "动作")
    if key in ENGINE_ACTIONS:
        raise ValueError(f"动作名「{key}」是内置名，换个带命名空间的名字")
    if _claim(key, source, "动作"):
        _ACTIONS[key] = fn
        if ai_allowed:
            _AI_ALLOWED.add(key)


def unregister_action(name: str, *, source: str = "plugin") -> bool:
    """按来源卸载一个动作（热重载用）。现在后端没有热重载，这是给将来留的干净出口。"""
    key = str(name or "").strip()
    from app.utils.pure.conditions import _OWNER

    if _OWNER.get(key) != source:
        return False
    _ACTIONS.pop(key, None)
    _AI_ALLOWED.discard(key)
    _OWNER.pop(key, None)
    return True


def ai_safe_actions() -> tuple[str, ...]:
    """AI 自写规则能用的动作 = 内置安全动作 + 注册方显式放行的动作。"""
    return SAFE_ACTIONS + tuple(sorted(_AI_ALLOWED))


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
    if source == "ai" and action not in ai_safe_actions():
        return False, (f"AI 写的规则只能用 {list(ai_safe_actions())}——"
                       f"{action} 要么会吞掉工具结果，要么没被注册方放行给 AI")
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


def explain(rules, ctx: dict, *, event: str = "tool_result", delivered=None,
            source: str = "plugin") -> list[dict]:
    """逐条给出判定结果与原因（dry-run / 排查用）。

    原因取值：
    - 规则/写法有问题的：rule_invalid / action_not_allowed / conditions_too_large /
      pattern_rejected / op_unknown（entry 里带 detail 原文）
    - 规则没问题但没跑起来的：event_mismatch / conditions_false / already_delivered
    - 命中：matched

    边界：注册时就被丢掉的规则不在 rules 里，这里看不到——那类用 validate_trigger 审。
    explain 管的是"已经登记进来的为什么不触发"。
    """
    from app.utils.pure.conditions import explain_conditions, reason_for_error

    seen = set(delivered or ())
    out: list[dict] = []
    for rule in rules or []:
        rid = rule_id_of(rule)
        ok, err = validate_trigger(rule, source=source)
        if not ok:
            out.append({"id": rid, "matched": False, "why": reason_for_error(err), "detail": err})
            continue
        when = (rule or {}).get("when") or {}
        if str(when.get("event") or event) != event:
            out.append({"id": rid, "matched": False, "why": "event_mismatch"})
            continue
        conditions = when.get("conditions")
        if conditions is not None:
            matched, reason = explain_conditions(conditions, ctx)
            if not matched:
                out.append({"id": rid, "matched": False, "why": reason})
                continue
        if scope_of(rule) == "frame" and rid in seen:
            out.append({"id": rid, "matched": False, "why": "already_delivered"})
            continue
        out.append({"id": rid, "matched": True, "why": "matched"})
    return out
