"""忙时中断的会话边界：一个 AI 同时在群和私信里，投递不能串会话

2026-11-25 的线上现象：AI 在群里跑一轮时用户发私信，私信被当成"忙时中断"塞进缓冲，
但那一轮是群、取不到它；兜底重排只在这个私信自己跑完时才触发，于是这条消息要等到
下次有人在这个私信里说话——用户看到的正是「我中途发消息她好像没看到」。

契约（backend/app/ai/executor.py 的 _active_runs / conversation_of）：
  * 「忙」是**按会话**的：别的会话在跑 ≠ 这个会话在跑；
  * 中断缓冲跨会话共用，但取的时候只取自己那个会话；
  * 通知（notice）不分会话，哪个会话来取都给。
"""
import pytest

pytestmark = pytest.mark.anyio


async def test_busy_is_per_conversation():
    from app.ai.executor import (
        is_agent_running, mark_agent_running, unmark_agent_running,
    )

    agent = 999001
    assert await is_agent_running(agent) is False
    await mark_agent_running(agent, ("group", 12))
    try:
        assert await is_agent_running(agent) is True, "不带会话：任一会话在跑就是忙"
        assert await is_agent_running(agent, ("group", 12)) is True
        assert await is_agent_running(agent, ("group", 13)) is False, "别的群忙不算这个群忙"
        assert await is_agent_running(agent, ("dm", "1_2")) is False, "群忙不算私信忙"
    finally:
        await unmark_agent_running(agent, ("group", 12))
    assert await is_agent_running(agent) is False


async def test_buffer_is_shared_but_drained_per_conversation():
    from app.ai.executor import (
        add_pending_interrupt, drain_pending_interrupts,
    )

    agent = 999002
    await add_pending_interrupt(agent, {
        "type": "user_message", "content": "群里的", "group_id": 12, "sender_id": 1})
    await add_pending_interrupt(agent, {
        "type": "user_message", "content": "私信里的", "session_id": "1_2", "sender_id": 2})
    await add_pending_interrupt(agent, {"type": "notice", "content": "能力变了"})

    mine = await drain_pending_interrupts(agent, group_id=12)
    assert [m["content"] for m in mine if m["type"] == "user_message"] == ["群里的"]
    assert any(m["type"] == "notice" for m in mine), "通知不分会话"

    rest = await drain_pending_interrupts(agent, session_id="1_2")
    assert [m["content"] for m in rest if m["type"] == "user_message"] == ["私信里的"]


async def test_leftover_dm_messages_are_requeued():
    """私信这一轮跑完还没投出去的中断 → 自己排一轮（不是丢掉）"""
    from app.ai import response_worker
    from app.ai.executor import add_pending_interrupt

    agent = 999003
    await add_pending_interrupt(agent, {
        "type": "user_message", "content": "第二条", "message_id": 7,
        "session_id": "1_2", "sender_id": 2})

    await response_worker._requeue_leftover_dm_interrupts(agent, "1_2", chain_depth=0)

    queued = []
    while True:
        try:
            queued.append(response_worker.message_queue.get_nowait())
        except Exception:
            break
    mine = [q for q in queued if q.get("session_id") == "1_2"]
    assert mine and mine[0]["content"] == "第二条", queued
