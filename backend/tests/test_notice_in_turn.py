"""变更通知的提前量：趁它在干活喊一声，账本那条照旧

规则：便签一定贴（账本条目，下一轮读得到），喊只是顺口一句（轮内可见、不进历史）；
自己当轮改的，下一轮那张贴纸上只写「已生效」，不再列变更量。
"""
import pytest

pytestmark = pytest.mark.anyio


async def test_shout_only_while_it_is_running():
    from app.ai.executor import (drain_pending_interrupts, mark_agent_running, shout_change,
                                 unmark_agent_running)

    try:
        assert await shout_change(47, "工具变了") is False            # 没在跑：不喊（账本照旧）
        await mark_agent_running(47, "group:69")
        assert await shout_change(47, "工具变了") is True
        got = await drain_pending_interrupts(47, group_id=70)         # 通知不分会话：哪个会话来取都给
        assert [m["type"] for m in got] == ["notice"], got
        assert got[0]["content"] == "工具变了"
    finally:
        await unmark_agent_running(47, "group:69")
        await drain_pending_interrupts(47)


async def test_notice_becomes_a_system_line_without_speaker():
    from app.ai.executor import interrupt_message_block

    block = interrupt_message_block({"type": "notice", "content": "工具变了"}, "古河渚")
    assert block == {"role": "system", "content": "工具变了"}


async def test_notice_leftover_is_dropped_not_requeued():
    """投递只负责投递：通知绝不能变成新一轮，也不必回写（账本那条才是底子）"""
    from app.ai.executor import add_pending_interrupt, drain_pending_interrupts
    from app.ai.response_worker import _requeue_leftover_interrupts, message_queue

    while not message_queue.empty():
        message_queue.get_nowait()
    await add_pending_interrupt(47, {"type": "notice", "content": "工具变了"})
    await _requeue_leftover_interrupts(47, 110, 69, 0)
    assert message_queue.empty(), "通知不能变成新一轮"
    assert await drain_pending_interrupts(47) == [], "通知也不必留着"


async def test_self_change_is_taken_once():
    """自己当轮改的：短句只给一次（第一个来读的状态），别每轮都短"""
    from app.services.capability_versioning import mark_self_change, take_self_change

    mark_self_change(47, "memory-index-47")
    assert take_self_change(47, "memory-index-47") is True
    assert take_self_change(47, "memory-index-47") is False
    assert take_self_change(47, "platform") is False
