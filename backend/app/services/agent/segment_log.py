"""状态段收尾落库：离开一个状态帧时，把这一段的样子留在它自己的桶里。

日志列表按状态帧身份归堆，而一条日志原本只对应一次模型调用，请求体在开轮时就定型了：
一轮中途 enter_state / pop_state 换掉的段落在日志里一点痕迹都没有。插件帧尤其明显——
「刷 B 站」那一段除了开轮时恰好在那帧里的一两次调用，别的什么都看不到。

口径是「离开哪个状态，就为哪个状态落一条」：内容取那一刻手上那份请求体，只把状态块换成
这一段的样子。链用的是**换帧之前**的快照（它 active 的正是被离开的帧），所以交接、doing、
TODO、预算读的都是当时那份，不是下一段的。

这些行不是模型调用：不花额度、不算 token、没有结局，来源记成 `switch`
（见 utils/pure/conversation_log 的归堆与裁剪，两者按同一列认桶）。
"""
from __future__ import annotations

import copy
import logging

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# 会换当前帧的工具。只有它们值得在执行前后各读一次状态链——其余工具不必付这份代价。
SWITCH_TOOLS = frozenset({
    "push_state", "enter_state", "pop_state", "close_state", "finish_frame", "restore_state",
})


async def record_leave(
    db: AsyncSession, agent, *,
    stack_before: list[dict] | None,
    messages: list[dict] | None,
    conversation_type: str = "group",
    group_id: int | None = None,
    session_id: str | None = None,
    user_id: int | None = None,
) -> int | None:
    """换了帧就把被离开的那一段落进它自己的桶；没换帧就什么都不做。返回新行 id。

    失败只记日志：这是给日志页补的一段记录，不该让工具执行本身失败。
    """
    try:
        return await _record_leave(db, agent, stack_before=stack_before, messages=messages,
                                   conversation_type=conversation_type, group_id=group_id,
                                   session_id=session_id, user_id=user_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("状态段收尾落库失败（不影响这一轮）: %s", e)
        return None


async def _record_leave(db, agent, *, stack_before, messages, conversation_type,
                        group_id, session_id, user_id) -> int | None:
    from app.utils.pure.state_stack import current, parse_state_summary, state_key_of

    left = current(stack_before or [])
    if not left:
        return None
    from app.services.agent.state_stack_service import get_current_frame

    # 帧 id 没变 = 这一下没有换帧（工具失败、只改内容、或销掉的是待交接的另一帧）
    after = await get_current_frame(db, agent.id)
    if after and str(after.get("id") or "") == str(left.get("id") or ""):
        return None

    from app.services.agent.state_stack_service import render_state_summary
    from app.services.content.conversation_log_service import save_conversation_log

    summary = await render_state_summary(db, agent.id, list(stack_before or []), budget_frame=left)
    body = _body_of(messages, summary, _leave_note(left, after))
    # 桶名以请求体里那块摘要为准：push_state 没写 label 的帧在摘要里渲染成 context_ref，
    # 行里若仍按 label 算就成了 `world|`——同一行两个口径，列表和正文对不上
    identity = parse_state_summary(summary)
    return await save_conversation_log(
        db, agent.id, body,
        conversation_type=conversation_type,
        group_id=group_id,
        session_id=session_id,
        token_usage=None,
        has_output=False,
        user_id=user_id,
        origin="switch",
        state_key=state_key_of(identity) or state_key_of(left),
    )


def _body_of(messages: list[dict] | None, summary: str, note: str) -> list[dict]:
    """这一段落下来的请求体：原样复制一份，状态块换成这一段的样子，末尾补一句去向。

    图片字节先洗掉（与本轮日志同一个口子）：账本里没人读那些 base64，只会把它撑大。
    """
    from app.utils.multimodal import strip_image_bytes
    from app.utils.pure.state_stack import is_state_summary

    body = strip_image_bytes(copy.deepcopy(messages or []))
    if summary:
        for msg in body:
            if msg.get("role") == "system" and is_state_summary(msg.get("content")):
                msg["content"] = summary
                break
        else:
            body.append({"role": "system", "content": summary})
    if note:
        body.append({"role": "system", "content": note})
    return body


def _leave_note(left: dict, after: dict | None) -> str:
    """一句人话说明这一段为什么到此为止——不然读日志的人只看见状态块换了个样子。"""
    if after:
        return f"（状态切换）离开 {_ident(left)} → 进入 {_ident(after)}，这一段到此结束。"
    return f"（状态切换）离开 {_ident(left)}，之后没有当前状态。"


def _ident(frame: dict | None) -> str:
    """帧身份说人话：`[type] label`；没写 label 的帧退回 context_ref，与摘要同一个口径。"""
    frame = frame or {}
    type_ = str(frame.get("type") or "?")
    label = str(frame.get("label") or frame.get("context_ref") or "").strip()
    return f"[{type_}] {label}".strip()
