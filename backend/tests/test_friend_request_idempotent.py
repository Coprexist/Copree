"""好友申请：重复接受不许炸会话。

线上事故（2026-10-28 复盘）：oxo 发给 eleven 的好友申请 #21 从六月挂到十月，
十一每次回私信都会顺手 accept 一遍 —— 关系其实早就在（2026-06-27 建过），
第二次 add 撞 uq_friendship，IntegrityError 抛在建 flush 上，整个事务随之报废。
那一轮 AI 连一句回复都没发出去，日志里只剩一行"Session 已回滚"，谁都看不出是好友申请干的。

这里钉三件事：
1. 关系已经在时，接受申请仍是成功（幂等），不抛唯一约束；
2. 没有关系时照旧建双向关系，申请状态确实被写成 accepted；
3. 工具失败弄坏事务后，_recover_session 能把会话救回来（否则整轮仍会静默消失）。

注意：自带 runner 不支持 pytest.raises，断言失败一律用手写的 try/except。
"""
import pytest

pytestmark = pytest.mark.anyio

AI_USER = 40     # 被申请的 AI
AGENT_ID = 24


async def _seed(db, *, already_friends: bool):
    from sqlalchemy import text

    from db_reset import clear

    await clear(db, "friendships", "friendship_requests", "dm_messages", "dm_sessions", "agents", "users")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES "
        "(0, '系统', 'x', 'system'), (1, '申请人', 'x', 'human'), (40, '十一', 'x', 'ai')"))
    await db.execute(text(
        "INSERT INTO agents (id, owner_id, name, user_id, discoverable) VALUES "
        f"({AGENT_ID}, 1, '十一', {AI_USER}, true)"))
    if already_friends:
        await db.execute(text(
            "INSERT INTO friendships (user_id, friend_type, friend_id) VALUES "
            f"(1, 'ai', {AI_USER}), ({AI_USER}, 'human', 1)"))
    await db.execute(text(
        "INSERT INTO friendship_requests (id, requester_id, target_type, target_id, status) VALUES "
        f"(21, 1, 'ai', {AI_USER}, 'pending')"))
    await db.commit()


async def _friendship_count(db) -> int:
    from sqlalchemy import text
    return int((await db.execute(text("SELECT count(*) FROM friendships"))).scalar_one())


async def _request_status(db, request_id: int = 21) -> str:
    from sqlalchemy import text
    return (await db.execute(text(
        "SELECT status FROM friendship_requests WHERE id = :i"), {"i": request_id})).scalar_one()


async def _expect_value_error(fn) -> None:
    try:
        await fn()
    except ValueError:
        return
    raise AssertionError("本该以业务错误拒绝，却放行了")


async def test_accept_is_idempotent_when_already_friends(migrated_db):
    """关系早就在：接受这条老申请仍然成功，好友数不翻倍"""
    from app.database import async_session
    from app.repositories.friend_repo import SQLAlchemyFriendRepository
    from app.services.social.friend_service import accept_friend_request

    async with async_session() as db:
        await _seed(db, already_friends=True)
        before = await _friendship_count(db)

        result = await accept_friend_request(
            friend_repo=SQLAlchemyFriendRepository(db), request_id=21, user_id=AI_USER,
        )
        await db.commit()

        assert result["status"] == "accepted"
        assert await _friendship_count(db) == before, "重复接受不该再插一份关系"
        assert await _request_status(db) == "accepted"


async def test_accept_still_creates_friendship_when_missing(migrated_db):
    """没有关系时照旧建双向关系：幂等不等于不干活"""
    from app.database import async_session
    from app.repositories.friend_repo import SQLAlchemyFriendRepository
    from app.services.social.friend_service import accept_friend_request

    async with async_session() as db:
        await _seed(db, already_friends=False)
        assert await _friendship_count(db) == 0

        await accept_friend_request(
            friend_repo=SQLAlchemyFriendRepository(db), request_id=21, user_id=AI_USER,
        )
        await db.commit()

        assert await _friendship_count(db) == 2
        assert await _request_status(db) == "accepted"


async def test_accept_twice_is_not_a_crash(migrated_db):
    """同一条申请被处理两遍（并发/重试）：第二遍是业务拒绝，不是唯一约束炸事务"""
    from app.database import async_session
    from app.repositories.friend_repo import SQLAlchemyFriendRepository
    from app.services.social.friend_service import accept_friend_request

    async with async_session() as db:
        await _seed(db, already_friends=False)
        repo = SQLAlchemyFriendRepository(db)
        await accept_friend_request(friend_repo=repo, request_id=21, user_id=AI_USER)
        await db.commit()

        await _expect_value_error(
            lambda: accept_friend_request(friend_repo=repo, request_id=21, user_id=AI_USER))
        # 报的是"状态不对"，会话没坏：还能继续查库
        assert await _request_status(db) == "accepted"


async def test_recover_session_revives_poisoned_transaction(migrated_db):
    """工具把事务写炸之后，会话能被救回来继续用——否则那一轮 AI 的回复会静默消失"""
    from sqlalchemy import text

    from app.database import async_session
    from app.models.agent import Agent
    from app.models.friendship import Friendship
    from app.tools.base import ToolRegistry

    async with async_session() as db:
        await _seed(db, already_friends=True)
        agent = await db.get(Agent, AGENT_ID)
        assert agent is not None

        # 亲手制造一次 flush 失败（唯一约束），让会话进入"必须先 rollback"的坏状态：
        # 线上那次崩溃正是这一个形状（工具 add 了已存在的关系，IntegrityError 抛在 flush 上）
        db.add(Friendship(user_id=1, friend_type="ai", friend_id=AI_USER))
        try:
            await db.flush()
        except Exception:
            pass
        assert not db.is_active, "前置条件：这一下应该把会话写坏"

        poisoned = False
        try:
            await db.execute(text("SELECT 1"))
        except Exception:
            poisoned = True
        assert poisoned, "前置条件：坏会话上任何一次入库都该失败"

        await ToolRegistry._recover_session(db, {"_agent": agent}, "handle_friend_request")

        assert db.is_active, "救回来之后会话要能继续用"
        assert agent.name == "十一", "agent 被 rollback 置为过期后要能继续读"
        assert (await db.execute(text("SELECT 1"))).scalar_one() == 1
