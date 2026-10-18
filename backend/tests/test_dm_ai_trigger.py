"""AI↔AI 私信触发：只受双向限额约束，不受「允许他人对话」影响。

规则在 docs/dev/ai_ai_dm_quota.md:34-36 —— human→AI 才走 allow_others_chat / own_key，
AI 之间只受双向限额。2026-10-06 的 41_116 会话就是踩坑现场：收信方「不允许他人对话 +
只收自带 Key」，而 AI 用户的 users.api_key_encrypted 恒为空（Key 存在 agents 上），
own_key 分支因此对 AI 发信人永远不成立 —— 收信方一次都没被唤醒，它那侧的对话日志整段缺失。
同一个函数里还压着两个毛病：限额先扣后判（被拒也计数）、系统提示传了不存在的
sender_type 参数（三个调用点全是死代码）。
"""
import pytest

pytestmark = pytest.mark.anyio

OWNER = 1
HUMAN = 2
SENDER_AI = 40       # 甲AI：发信方
RECEIVER_AI = 41     # 乙AI：收信方（不允许他人对话 + own_key）


async def _seed(db, *, receive_daily: int = 20):
    from sqlalchemy import text

    from db_reset import clear

    await clear(db, "agent_state_stack", "dm_messages", "dm_sessions", "agents", "users")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES "
        "(0, '系统', 'x', 'system'), (1, '主人', 'x', 'human'), (2, '路人', 'x', 'human'), "
        "(40, '甲AI', 'x', 'ai'), (41, '乙AI', 'x', 'ai')"))
    await db.execute(text(
        "INSERT INTO agents (id, owner_id, name, user_id, allow_others_chat, disallow_mode) VALUES "
        "(24, 1, '甲AI', 40, true, 'strict'), (25, 1, '乙AI', 41, false, 'own_key')"))
    # 只开「每天」这一个维度，收发各一份；其余维度 0 = 不启用
    await db.execute(text(
        "UPDATE agents SET dm_quota_config = jsonb_build_object("
        "  'send', jsonb_build_object('daily', 20, 'weekly', 0, 'creator_chat', 0),"
        "  'receive', jsonb_build_object('daily', CAST(:d AS int), 'weekly', 0, 'creator_chat', 0))"
        " WHERE id = 25"), {"d": receive_daily})
    await db.commit()


class _Queue:
    """替代 worker 的 message_queue：这里只关心「有没有交给它」"""

    def __init__(self):
        self.items = []

    def put_nowait(self, item):
        self.items.append(item)


async def _trigger(db, session_id: str, sender_id: int) -> _Queue:
    """调一次触发管道，返回收集到的队列事件（worker 与「正在跑」都打桩成空闲）"""
    import app.ai.executor as executor
    import app.ai.response_worker as worker
    from app.routers.dm import _maybe_trigger_dm_ai_reply

    queue = _Queue()
    original_queue, original_running = worker.message_queue, executor.is_agent_running

    async def _idle(*args, **kwargs):
        return False

    worker.message_queue, executor.is_agent_running = queue, _idle
    try:
        await _maybe_trigger_dm_ai_reply(db, session_id, {"id": 1, "content": "在吗"}, sender_id)
    finally:
        worker.message_queue, executor.is_agent_running = original_queue, original_running
    return queue


async def _quota_state(db) -> dict:
    from sqlalchemy import text
    return (await db.execute(text("SELECT dm_quota_state FROM agents WHERE id = 25"))).scalar()


async def test_ai_sender_wakes_a_receiver_that_refuses_humans(migrated_db):
    """AI 发来的私信照样唤醒：own_key 是 human→AI 的门槛，拦不到 AI 之间"""
    from app.chat.dm import get_or_create_dm_session
    from app.database import async_session

    async with async_session() as db:
        await _seed(db)
        sid = (await get_or_create_dm_session(db, SENDER_AI, RECEIVER_AI))["session_id"]

        queue = await _trigger(db, sid, SENDER_AI)

        assert len(queue.items) == 1, "AI 发来的私信没有唤醒收信方"
        assert queue.items[0]["conversation_type"] == "dm"
        assert queue.items[0]["sender_type"] == "ai"
        state = await _quota_state(db)
        assert state["receive"]["daily_count"] == 1, state


async def test_exhausted_quota_neither_wakes_nor_charges(migrated_db):
    """限额用尽：不唤醒、不计数，但要留下「有人找过」的会话帧"""
    from sqlalchemy import text
    from app.chat.dm import get_or_create_dm_session
    from app.database import async_session

    async with async_session() as db:
        await _seed(db, receive_daily=1)
        sid = (await get_or_create_dm_session(db, SENDER_AI, RECEIVER_AI))["session_id"]

        assert len((await _trigger(db, sid, SENDER_AI)).items) == 1
        assert (await _quota_state(db))["receive"]["daily_count"] == 1

        refused = await _trigger(db, sid, SENDER_AI)

        assert refused.items == [], "限额用尽却还在唤醒"
        assert (await _quota_state(db))["receive"]["daily_count"] == 1, "被拒的轮次不该再计数"
        stack = (await db.execute(text(
            "SELECT state_stack FROM agents WHERE id = 25"))).scalar() or []
        assert any(f.get("context_ref") == sid for f in stack), "被限额拦下时没有记「有人找过」"


async def test_human_without_key_is_refused_and_told(migrated_db):
    """human→AI 的老规则不变：没自有 Key 不唤醒；这回提示真能发进会话"""
    from sqlalchemy import text
    from app.chat.dm import get_or_create_dm_session
    from app.database import async_session

    async with async_session() as db:
        await _seed(db)
        sid = (await get_or_create_dm_session(db, HUMAN, RECEIVER_AI))["session_id"]

        queue = await _trigger(db, sid, HUMAN)

        assert queue.items == [], "own_key 模式对没有 Key 的人类应当静默不触发"
        notice = (await db.execute(text(
            "SELECT sender_id, message_type FROM dm_messages "
            "WHERE session_id = :sid AND message_type = 'system'"), {"sid": sid})).all()
        assert len(notice) == 1 and notice[0][0] == 0, notice
