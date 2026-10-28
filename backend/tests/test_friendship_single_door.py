"""好友关系只有一扇门：加好友这件事只在 friend_repo.add_friendship 里落地。

2026-10-28 的事故就是"两扇门"：friend_service 无条件插入、agent_service 自己判存在，
走岔的那一边撞了唯一约束、把 AI 的一整轮回复弄没。现在两处都走同一个入口，
入口本身用 ON CONFLICT DO NOTHING —— 唯一约束由数据库守，重复插入不再是一次崩溃。

注意：自带 runner 不支持 pytest.raises，断言失败一律手写 try/except。
"""
import pytest

pytestmark = pytest.mark.anyio

OWNER = 1


async def _seed(db):
    from sqlalchemy import text

    from db_reset import clear

    await clear(db, "friendships", "friendship_requests", "dm_messages", "dm_sessions", "agents", "users")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES "
        "(0, '系统', 'x', 'system'), (1, '主人', 'x', 'human')"))
    # create_agent 会再建一个 AI 用户，走的是自增序列：显式插入过 id 之后要把序列推到后面
    await db.execute(text("SELECT setval('users_id_seq', 100)"))
    await db.commit()


async def _pairs(db) -> list[tuple]:
    from sqlalchemy import text
    rows = (await db.execute(text(
        "SELECT user_id, friend_type, friend_id FROM friendships ORDER BY user_id"))).all()
    return [tuple(r) for r in rows]


async def test_creating_an_ai_writes_both_directions_once(migrated_db):
    """建 AI：双向关系各一条，不多不少"""
    from app.database import async_session
    from app.services.agent.agent_service import create_agent

    async with async_session() as db:
        await _seed(db)
        agent = await create_agent(db, owner_id=OWNER, name=" newcomer ", is_admin=True)
        await db.commit()

        pairs = await _pairs(db)
        assert (OWNER, "ai", agent.user_id) in pairs
        assert (agent.user_id, "human", OWNER) in pairs
        assert len(pairs) == 2, pairs


async def test_add_friendship_twice_is_a_no_op(migrated_db):
    """重复加同一对：第二次什么都不做，也不抛唯一约束"""
    from app.database import async_session
    from app.repositories.friend_repo import SQLAlchemyFriendRepository

    async with async_session() as db:
        await _seed(db)
        repo = SQLAlchemyFriendRepository(db)
        await repo.add_friendship(OWNER, "ai", 40)
        await repo.add_friendship(OWNER, "ai", 40)
        await db.commit()

        assert await _pairs(db) == [(OWNER, "ai", 40)]
