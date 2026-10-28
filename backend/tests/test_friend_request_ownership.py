"""好友申请的归属：谁能替一条申请做决定。

2026-10-28 复盘里剩下的那个口子：`accept_friend_request` 只对 `target_type == "human"`
校验归属，发给 AI 的申请谁传都能接受——认不认这条申请，本该由收件方（AI 或它的主人）说了算。
判据现在只有一处（`friend_service._may_resolve_request`），接受与拒绝共用。

另外钉住一条容易写漏的边界：**发起者不能替对方答应**，否则一条申请自己点一下就成了。
"""
import pytest

pytestmark = pytest.mark.anyio

OWNER = 1        # agent 的主人（人类）
AI_USER = 40     # AI 自己的账号
REQUESTER = 3    # 申请人
STRANGER = 4     # 与这条申请无关的人
AGENT_ID = 24
REQUEST_ID = 21


async def _seed(db):
    from sqlalchemy import text

    from db_reset import clear

    await clear(db, "friendships", "friendship_requests", "agents", "users")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES "
        f"(0, '系统', 'x', 'system'), ({OWNER}, '主人', 'x', 'human'), "
        f"({REQUESTER}, '申请人', 'x', 'human'), ({STRANGER}, '路人', 'x', 'human'), "
        f"({AI_USER}, '十一', 'x', 'ai')"))
    await db.execute(text(
        "INSERT INTO agents (id, owner_id, name, user_id, discoverable) VALUES "
        f"({AGENT_ID}, {OWNER}, '十一', {AI_USER}, true)"))
    await db.execute(text(
        "INSERT INTO friendship_requests (id, requester_id, target_type, target_id, status) VALUES "
        f"({REQUEST_ID}, {REQUESTER}, 'ai', {AI_USER}, 'pending')"))
    await db.commit()


async def _expect_value_error(fn) -> None:
    try:
        await fn()
    except ValueError:
        return
    raise AssertionError("本该以业务错误拒绝，却放行了")


async def _status(db) -> str:
    from sqlalchemy import text
    return (await db.execute(text(
        "SELECT status FROM friendship_requests WHERE id = :i"), {"i": REQUEST_ID})).scalar_one()


async def test_a_stranger_cannot_decide_for_an_ai(migrated_db):
    """发给 AI 的申请不认关系户：无关的人接受不了，申请原样挂着"""
    from app.database import async_session
    from app.repositories.friend_repo import SQLAlchemyFriendRepository
    from app.services.social.friend_service import accept_friend_request

    async with async_session() as db:
        await _seed(db)
        repo = SQLAlchemyFriendRepository(db)

        await _expect_value_error(
            lambda: accept_friend_request(friend_repo=repo, request_id=REQUEST_ID, user_id=STRANGER))
        assert await _status(db) == "pending"


async def test_the_requester_cannot_accept_on_the_targets_behalf(migrated_db):
    """自己发出去的申请：能撤回/拒绝，不能替对方答应"""
    from app.database import async_session
    from app.repositories.friend_repo import SQLAlchemyFriendRepository
    from app.services.social.friend_service import accept_friend_request

    async with async_session() as db:
        await _seed(db)
        repo = SQLAlchemyFriendRepository(db)

        await _expect_value_error(
            lambda: accept_friend_request(friend_repo=repo, request_id=REQUEST_ID, user_id=REQUESTER))
        assert await _status(db) == "pending"


async def test_the_ai_and_its_owner_can_decide(migrated_db):
    """收件方两个主体都认：AI 自己的账号，以及替它负责的主人"""
    from app.database import async_session
    from app.repositories.friend_repo import SQLAlchemyFriendRepository
    from app.services.social.friend_service import accept_friend_request, reject_friend_request

    async with async_session() as db:
        await _seed(db)
        repo = SQLAlchemyFriendRepository(db)

        # 主人代它通过：与 AI 自己点通过等价
        result = await accept_friend_request(
            friend_repo=repo, request_id=REQUEST_ID, user_id=OWNER)
        await db.commit()
        assert result["status"] == "accepted"
        assert await _status(db) == "accepted"

        # AI 自己拒绝另一条（重新挂一条 pending）
        from sqlalchemy import text
        await db.execute(text(
            "INSERT INTO friendship_requests (id, requester_id, target_type, target_id, status) "
            f"VALUES ({REQUEST_ID + 1}, {STRANGER}, 'ai', {AI_USER}, 'pending')"))
        await db.commit()
        rejected = await reject_friend_request(
            friend_repo=repo, request_id=REQUEST_ID + 1, user_id=AI_USER)
        await db.commit()
        assert rejected["status"] == "rejected"
