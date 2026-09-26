"""触发组合规则的执行 —— 工具事件。

规则全在内存里（平台内置 + 插件声明 + AI 自写），热路径不查库；状态只在「有规则盯着这个工具」
时读写。状态挂在当前状态帧上（tool_uses / delivered）：compact/clear 后帧重建，帧内计数与"投过没投过"
一起归零——这正是 scope=frame 要的语义，不必另建状态表。

任何一步失败都只记日志：触发规则是锦上添花，不能让一次工具调用失败。
"""
from __future__ import annotations

import logging

from app.utils.pure import trigger_rules

logger = logging.getLogger(__name__)


def rules_for(tool_name: str) -> list[dict]:
    """候选规则。没写明盯着哪个工具的规则对所有工具生效（targets_of 返回 None）。"""
    out: list[dict] = []
    for rule in trigger_rules.all_rules():
        targets = trigger_rules.targets_of(rule)
        if targets is None or tool_name in targets:
            out.append(rule)
    return out


def _ctx(tool_name: str, agent_id: int, result: dict, uses: int) -> dict:
    """工具事件的 ctx。字段名带 _in_frame：帧重建后计数归零，别把它说成"本会话"。"""
    return {
        "event": "tool_result",
        "tool": tool_name,
        "ok": not result.get("error", False),
        "calls_in_frame": uses,
        "first_in_frame": uses == 1,
        "agent_id": agent_id,
    }


async def after_tool_result(db, agent_id: int, tool_name: str, result: dict) -> dict:
    """工具执行完统一过一遍规则，返回（可能被补丁过的）工具结果。"""
    if not isinstance(result, dict):
        return result
    rules = rules_for(tool_name)
    if not rules:
        return result

    from app.services.agent.state_stack_service import load_trigger_state, save_trigger_state

    state = await load_trigger_state(db, agent_id)
    uses = int((state.get("tool_uses") or {}).get(tool_name, 0)) + 1
    state["tool_uses"][tool_name] = uses
    ctx = _ctx(tool_name, agent_id, result, uses)

    patch: dict = {}
    notices: list[str] = []
    executed: list[str] = []
    for rule in trigger_rules.hits(rules, ctx):
        rid = trigger_rules.rule_id_of(rule)
        if trigger_rules.scope_of(rule) == "frame" and rid in (state.get("delivered") or {}):
            continue
        do = rule.get("do") or {}
        action = str(do.get("action") or "")
        try:
            if action == "deliver":
                text = str(do.get("text") or "").strip()
                if text:
                    notices.append(text)
            elif action == "silent":
                patch["silent"] = True
            else:
                handler = trigger_rules.action_handler(action)
                if handler is None:
                    continue
                extra = handler(ctx, do, dict(result))
                if isinstance(extra, dict):
                    # 自定义动作只能写 _trigger 命名空间：覆盖不了 success / url / 结果本体
                    patch.setdefault("_trigger", {})[action] = extra
        except Exception as e:  # noqa: BLE001 —— 插件动作出错不能拖垮工具调用
            logger.warning("触发规则动作失败 %s/%s: %s", rid, action, e)
            continue
        state["delivered"][rid] = True
        executed.append(rid)

    await save_trigger_state(db, agent_id, state)
    if patch.pop("silent", False):
        result = {"success": True, "silent": True}
    elif patch:
        result = {**result, **patch}
    if notices:
        result = {**result, "notice": "\n".join(notices)}
    if executed:
        logger.info("触发组合规则命中 %s → %s", tool_name, executed)
    return result


async def explain_tool_result(db, agent_id: int, tool_name: str,
                              result: dict | None = None) -> list[dict]:
    """dry-run：会命中哪些规则、其余为什么没命中。不写状态、不改结果。

    排查"规则怎么没生效"用这个，别靠猜——不命中也要有个说得出口的原因。
    """
    rules = rules_for(tool_name)
    if not rules:
        return []
    from app.services.agent.state_stack_service import load_trigger_state

    state = await load_trigger_state(db, agent_id)
    uses = int((state.get("tool_uses") or {}).get(tool_name, 0)) + 1
    ctx = _ctx(tool_name, agent_id, result or {}, uses)
    return trigger_rules.explain(rules, ctx, delivered=state.get("delivered"))
