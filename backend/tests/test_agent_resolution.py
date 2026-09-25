"""
群成员 ID 解析测试 — member_id 是 user_id（不是 agent.id）
防止"@涵吾珑 触发成任熠航"事故复发（2026-08-07）

核心语义：group_members.member_id = agent.user_id（7-3 统一）；
解析时先按 user_id 查，fallback agent.id（兼容旧数据）。
"""
import pytest

pytestmark = pytest.mark.anyio


async def _seed(db):
    from sqlalchemy import text
    from db_reset import clear

    await clear(db, "agents", "users")
    await db.execute(text("""
        INSERT INTO users (id, username, password_hash, type) VALUES
        (1, '测试用户', 'x', 'human'),
        (4, '涵吾珑', 'x', 'ai'),
        (6, '任熠航', 'x', 'ai'),
        (9, 'test', 'x', 'ai'),
        (15, '234', 'x', 'ai')
    """))
    await db.execute(text("""
        INSERT INTO agents (id, owner_id, name, user_id, discoverable) VALUES
        (1, 1, '涵吾珑', 4, true),
        (4, 1, '任熠航', 6, true),
        (6, 1, 'test', 9, true),
        (9, 1, '234', 15, true)
    """))
    await db.commit()


async def _resolve(db, member_id: int):
    """复刻 response_worker / message.py 修复后的解析逻辑：先 user_id，fallback agent.id"""
    from sqlalchemy import select
    from app.models.agent import Agent as AgentModel
    r = await db.execute(select(AgentModel).where(AgentModel.user_id == member_id))
    a = r.scalar_one_or_none()
    if a is None:
        a = await db.get(AgentModel, member_id)
    return a


async def test_resolve_by_user_id(migrated_db):
    """user_id 命中（主语义）：member_id=4 → 涵吾珑（不是任熠航）"""
    from app.database import async_session
    async with async_session() as db:
        await _seed(db)
        a = await _resolve(db, 4)
        assert a is not None and a.name == "涵吾珑", f"解析错误: {a}"
        a = await _resolve(db, 15)
        assert a is not None and a.name == "234", f"解析错误: {a}"


async def test_resolve_fallback_agent_id(migrated_db):
    """fallback：member_id 是 agent.id 且不是任何 user_id → 按 id 查"""
    from app.database import async_session
    async with async_session() as db:
        await _seed(db)
        # 造一个"旧格式"成员：member_id=5（假设 agent.id=5 存在）
        from sqlalchemy import text
        await db.execute(text("INSERT INTO users (id, username, password_hash, type) VALUES (55, '旧格式用户', 'x', 'ai')"))
        await db.execute(text("INSERT INTO agents (id, owner_id, name, user_id, discoverable) VALUES (5, 1, '旧格式AI', 55, true)"))
        await db.commit()
        a = await _resolve(db, 5)  # 5 不是任何 user_id → fallback agent.id=5
        assert a is not None and a.name == "旧格式AI", f"fallback 解析错误: {a}"
