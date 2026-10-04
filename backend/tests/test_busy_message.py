"""AI 正在思考时到达的消息：投给它当前那一轮；没赶上的自成一轮——一条都不丢

踩过的坑：response_worker 拿不到 claim 就 continue，注释写着"LLM 跑完自然看到"——而跑着的
那一轮开局就把上下文构建好了、中途不重读群历史，所以消息既没唤醒它、它也没看见。
2026-10-04 群 69 实测：同一句话连发三条（2145/2147/2149），只回了 2150 一条。
"""
import pytest
from sqlalchemy import text

pytestmark = pytest.mark.anyio


async def _seed(db, *, ai_user_ids=(110,)):
    from db_reset import clear

    await clear(db, "group_members", "groups", "agents", "users")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES "
        "(1, '主人', 'x', 'human'), (113, '书爱', 'x', 'human'), (110, '古河渚', 'x', 'ai')"))
    await db.execute(text(
        "INSERT INTO groups (id, name, owner_type, owner_id, avatar_mode, include_ai_in_avatar, "
        "searchable, auto_approve_join, approve_invites) "
        "VALUES (69, '群', 'human', 1, 'default', true, true, true, true)"))
    await db.execute(text(
        "INSERT INTO agents (id, owner_id, name, user_id, discoverable) "
        "VALUES (47, 1, '古河渚', 110, true)"))
    for uid in ai_user_ids:
        await db.execute(text(
            "INSERT INTO group_members (group_id, member_type, member_id, role) "
            "VALUES (69, 'ai', :uid, 'member')"), {"uid": uid})
    await db.commit()


def _event(content="<@!110> 在吗", message_id=2147, only=None):
    event = {
        "conversation_type": "group", "group_id": 69, "message_id": message_id,
        "content": content, "sender_type": "human", "sender_id": 113, "chain_depth": 0,
    }
    if only is not None:
        event["only_ai_ids"] = only
    return event


async def test_busy_ai_gets_the_message_injected_into_its_running_turn(migrated_db):
    """正在跑一轮的 AI：消息进它当前那一轮的中断缓冲（下一轮 LLM 调用前注入），不再被丢掉"""
    from app.ai.chat_chain import chat_chain_manager
    from app.ai.executor import drain_pending_interrupts
    from app.ai.response_worker import _process_group_event
    from app.database import async_session

    async with async_session() as db:
        await _seed(db)
        assert chat_chain_manager.try_claim(110, 69)              # 她正在这个群里跑着一轮
        try:
            await _process_group_event(db, _event())
            injected = await drain_pending_interrupts(47)
            assert [m["content"] for m in injected] == ["<@!110> 在吗"], injected
            assert injected[0]["message_id"] == 2147
            assert injected[0]["sender_name"] == "书爱", injected[0]   # 名字写对，提示里才是人名
        finally:
            chat_chain_manager.release_claim(110, 69)
            await drain_pending_interrupts(47)


async def test_leftover_message_gets_its_own_turn_after_the_run(migrated_db):
    """落在最后一次注入之后的消息（收尾轮尤其）：本轮收尾时排成独立一轮，不用用户再发一条"""
    from app.ai.executor import add_pending_interrupt, drain_pending_interrupts
    from app.ai.response_worker import _requeue_leftover_interrupts, message_queue
    from app.database import async_session

    async with async_session() as db:
        await _seed(db)
        while not message_queue.empty():                          # 队列里别的测试的残留先清掉
            message_queue.get_nowait()
        await add_pending_interrupt(47, {
            "type": "user_message", "content": "收尾轮之后到的", "message_id": 2149,
            "group_id": 69, "sender_id": 113, "sender_name": "书爱",
        })
        await _requeue_leftover_interrupts(47, 110, 69, 0)

        assert message_queue.qsize() == 1
        event = message_queue.get_nowait()
        assert event["only_ai_ids"] == [110] and event["message_id"] == 2149, event
        assert event["content"] == "收尾轮之后到的"
        assert await drain_pending_interrupts(47) == [], "重排过的消息不能还在缓冲里（否则会投两遍）"


async def test_drain_only_takes_this_conversation():
    """缓冲是跨会话共用的：按会话取，别把私信的中断拼进群的轮次里"""
    from app.ai.executor import add_pending_interrupt, drain_pending_interrupts

    await add_pending_interrupt(47, {"type": "user_message", "content": "群的", "group_id": 69})
    await add_pending_interrupt(47, {"type": "user_message", "content": "私信的", "session_id": "dm:1_2"})
    try:
        got = await drain_pending_interrupts(47, group_id=69)
        assert [m["content"] for m in got] == ["群的"], got
        rest = await drain_pending_interrupts(47)
        assert [m["content"] for m in rest] == ["私信的"], rest
    finally:
        await drain_pending_interrupts(47)


def test_only_ai_ids_limits_the_requeued_turn():
    """重排的事件只叫当时忙着的那个 AI：同群别人早就处理过这条消息了"""
    from app.ai.response_worker import _filter_by_only

    assert _filter_by_only([110, 111, 112], [111]) == [111]
    assert _filter_by_only([110, 111], None) == [110, 111]
    assert _filter_by_only([110, 111], []) == [110, 111]
    assert _filter_by_only([110], [999]) == []


async def test_each_busy_message_becomes_its_own_block(migrated_db):
    """忙时到了多条：一条一块、按到达顺序（不合并——AI 要分得清谁在第几条说的），并带 msg_id"""
    from app.ai.chat_chain import chat_chain_manager
    from app.ai.executor import drain_pending_interrupts, interrupt_message_block
    from app.ai.response_worker import _process_group_event
    from app.database import async_session

    async with async_session() as db:
        await _seed(db)
        assert chat_chain_manager.try_claim(110, 69)
        try:
            await _process_group_event(db, _event(content="<@!110> 第一句", message_id=2147))
            await _process_group_event(db, _event(content="<@!110> 第二句", message_id=2149))
            injected = await drain_pending_interrupts(47, group_id=69)
            assert len(injected) == 2, injected
            blocks = [interrupt_message_block(pm, "古河渚")["content"] for pm in injected]
            assert "第一句" in blocks[0] and "第二句" in blocks[1], blocks
            assert "[msg_id=2147]" in blocks[0] and "[msg_id=2149]" in blocks[1], blocks
            assert "书爱（id=113）" in blocks[0], blocks[0]      # 说话人名字与 id 都要能认出来
            assert all(b.startswith("[") for b in blocks), blocks  # 带时间戳的一行
        finally:
            chat_chain_manager.release_claim(110, 69)
            await drain_pending_interrupts(47)


async def test_interrupt_buffer_is_capped():
    """缓冲有上限：超了丢最旧并留痕（消息本身还在群里，下一轮历史照常看得到）"""
    from app.ai.executor import MAX_PENDING_INTERRUPTS, add_pending_interrupt, drain_pending_interrupts

    try:
        for i in range(MAX_PENDING_INTERRUPTS + 5):
            await add_pending_interrupt(47, {"type": "user_message", "content": f"第{i}条", "group_id": 69})
        kept = await drain_pending_interrupts(47, group_id=69)
        assert len(kept) == MAX_PENDING_INTERRUPTS, len(kept)
        assert kept[0]["content"] == "第5条", kept[0]           # 最旧的 5 条被丢
        assert kept[-1]["content"] == f"第{MAX_PENDING_INTERRUPTS + 4}条", kept[-1]
    finally:
        await drain_pending_interrupts(47)
