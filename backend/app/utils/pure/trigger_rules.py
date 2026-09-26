"""触发组合规则 —— 条件组合 + 动作，一个引擎三种来源。

规则形状（插件声明、平台内置、AI 自写的决策技能共用）：
    {"when": <条件树>, "do": {"action": ...}, "once": "context" | "never"}

为什么要有这一层：此前"什么时候给 AI 讲一句、讲几次"散在四处——能力变更通知（按版本投一次）、
跨状态便签（40 次调用内投一次）、决策技能命中提示、各事件链路自己拼 note。形状其实相同，
接口各不相同。这里把它们收敛成一条规则：条件组合 + 动作 + 投几次。

条件和动作都可以"用现成的"或"自己手搓"：
- 条件：内置字段与运算见 utils/pure/conditions.py，另可用 register_op / register_predicate 扩
- 动作：register_action(name, handler) 注册自己的动作，handler(ctx, do, result) 返回要给结果打补丁的 dict

once 语义：
- context（默认）：投一次，状态挂当前状态帧——compact/clear 后帧重建，自然再投一次
- never：每次命中都投
（agent / version 两个粒度等状态层扩展，暂不开放，写了会被校验拒绝）

ctx 字段由事件源提供；工具事件：tool / ok / tool_calls / first / agent_id。
"""
from __future__ import annotations

import logging

from app.utils.pure.conditions import match_conditions

logger = logging.getLogger(__name__)

# 事件源：工具调用前 / 工具调用后（其余事件仍由决策技能自己的情景表承载，共用同一求值器）
TOOL_EVENTS = ("tool_call", "tool_result")
# 引擎直接执行的动作；其余动作交给各事件源自带的执行者（决策技能那四个）
ENGINE_ACTIONS = ("deliver", "silent")
# 已实现的状态粒度；agent / version 待状态层扩展
ONCE_VALUES = ("context", "never")
DEFAULT_ONCE = "context"

# 平台内置规则与插件声明规则：都走内存，工具调用是高频路径，不能每次查库
_BUILTIN: list[dict] = []
_PLUGIN: list[dict] = []
# 自定义动作：{名字: fn(ctx, do, result) -> dict | None}
_ACTIONS: dict[str, object] = {}


def register_action(name: str, fn) -> None:
    """登记一个动作。fn(ctx, do, result) 返回要合并进工具结果的补丁（None = 不改）。"""
    key = str(name or "").strip()
    if not key:
        raise ValueError("动作名不能为空")
    _ACTIONS[key] = fn


def action_names() -> tuple[str, ...]:
    return ENGINE_ACTIONS + tuple(_ACTIONS)


def action_handler(name: str):
    return _ACTIONS.get(str(name or ""))


def register_builtin(rules) -> None:
    """登记平台内置规则（按 id 幂等，重复导入不会堆叠）。"""
    _extend(_BUILTIN, rules)


def register_plugin(rules) -> None:
    """登记插件声明的规则（ToolPlugin.triggers 走这里）。"""
    _extend(_PLUGIN, rules)


def _extend(target: list[dict], rules) -> None:
    for rule in rules or []:
        if not isinstance(rule, dict):
            continue
        rule_id = rule_id_of(rule)
        if any(rule_id_of(r) == rule_id for r in target):
            continue
        ok, err = validate_trigger(rule)
        if not ok:
            logger.warning("触发规则无效，已跳过：%s（%s）", rule_id, err)
            continue
        target.append(rule)


def rule_id_of(rule: dict) -> str:
    """规则标识：显式 id 优先，否则用「事件 + 动作 + 文本」拼一个稳定指纹。"""
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


def all_rules(extra=()) -> list[dict]:
    """全部可用规则：AI 自写的（extra，调用方查库给）+ 内置 + 插件。"""
    return [*list(extra or []), *_BUILTIN, *_PLUGIN]


def targets_of(rule: dict) -> set[str] | None:
    """规则盯着哪些工具；None = 不挑工具（任何工具都要过一遍）。

    只为省查询：没有规则盯着的工具，调用时连状态都不用读。
    """
    when = (rule or {}).get("when") or {}
    conditions = when.get("conditions")
    found: set[str] = set()
    stack = [conditions] if conditions else []
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            continue
        for key, value in node.items():
            if key in ("and", "or"):
                stack.extend(value if isinstance(value, list) else [])
            elif key == "not":
                stack.append(value)
            elif key == "tool" and isinstance(value, str):
                found.add(value)
    if not conditions:
        return None
    return found or None


def validate_trigger(rule: dict) -> tuple[bool, str]:
    """校验一条触发规则；写错当场拒绝并说清可选值，不留给运行期安静地不触发。"""
    if not isinstance(rule, dict):
        return False, "规则必须是对象"
    when = rule.get("when")
    if not isinstance(when, dict) or not when:
        return False, "when 必填（条件树或事件对象）"
    event = str(when.get("event") or "").strip()
    if event and event not in TOOL_EVENTS:
        return False, f"when.event 只能是 {list(TOOL_EVENTS)}（其余事件归决策技能的情景表）"
    conditions = when.get("conditions")
    if conditions is not None and not isinstance(conditions, dict):
        return False, "when.conditions 必须是条件对象"
    do = rule.get("do") or {}
    action = str(do.get("action") or "")
    if not action:
        return False, "do.action 必填"
    if action == "deliver" and not str(do.get("text") or "").strip():
        return False, "deliver 需要 text（要投给 AI 的那句话）"
    if action not in action_names():
        return False, f"do.action 只能是 {list(action_names())} 之一，或先用 register_action 注册"
    once = str(rule.get("once") or DEFAULT_ONCE)
    if once not in ONCE_VALUES:
        return False, f"once 只能是 {list(ONCE_VALUES)}（agent / version 两个粒度待状态层扩展）"
    return True, ""


def hits(rules, ctx: dict, *, event: str = "tool_result") -> list[dict]:
    """命中且条件满足的规则（按登记顺序）。"""
    out: list[dict] = []
    for rule in rules or []:
        when = (rule or {}).get("when") or {}
        rule_event = str(when.get("event") or event)
        if rule_event != event:
            continue
        conditions = when.get("conditions")
        if conditions is None or match_conditions(conditions, ctx):
            out.append(rule)
    return out


def once_of(rule: dict) -> str:
    once = str((rule or {}).get("once") or DEFAULT_ONCE)
    return once if once in ONCE_VALUES else DEFAULT_ONCE
