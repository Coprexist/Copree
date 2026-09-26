"""私信权限：AI 主动私信生人必须被拒（提示加好友），系统通知与既有会话不受影响。

规则与背景见 backend/app/chat/dm.py:_require_friendship 的 docstring。
"""
import pytest

pytestmark = pytest.mark.anyio

AI_USER = 40          # AI
AI_USER_2 = 41        # 另一个 AI
OWNER = 1             # AI 的主人（创建 AI 时自动成为好友）
STRANGER = 2          # 与 AI 无任何关系的人
FRIEND = 3            # 与 AI 互为好友的人


async def _seed(db):
    from sqlalchemy import text

    from db_reset import clear

    await clear(db, "friendships", "dm_messages", "dm_sessions", "agents", "users")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES "
        "(0, '系统', 'x', 'system'), (1, '主人', 'x', 'human'), (2, '生人', 'x', 'human'), "
        "(3, '好友', 'x', 'human'), (40, '甲AI', 'x', 'ai'), (41, '乙AI', 'x', 'ai')"))
    await db.execute(text(
        "INSERT INTO agents (id, owner_id, name, user_id, discoverable) VALUES "
        "(24, 1, '甲AI', 40, true), (25, 2, '乙AI', 41, true)"))
    # 与 agent_service 建 AI 时写的一致，外加一条"与生人之外的普通好友"
    await db.execute(text(
        "INSERT INTO friendships (user_id, friend_type, friend_id) VALUES "
        "(1, 'ai', 40), (40, 'human', 1), (40, 'human', 3), (3, 'ai', 40)"))
    await db.commit()


async def _reject(fn) -> str:
    try:
        await fn()
    except ValueError as e:
        return str(e)
    raise AssertionError("本该被拒绝，却放行了")


async def test_ai_cannot_open_a_dm_with_a_stranger(migrated_db):
    """AI → 生人：拒绝，并告诉它去加好友"""
    from app.chat.dm import get_or_create_dm_session
    from app.database import async_session

    async with async_session() as db:
        await _seed(db)
        msg = await _reject(lambda: get_or_create_dm_session(db, AI_USER, STRANGER))
        assert "还不是好友" in msg and "send_friend_request" in msg, msg


async def test_ai_can_dm_its_owner_and_its_friends(migrated_db):
    """主人与好友都放行（主人那条本来就是好友，不靠特例）"""
    from app.chat.dm import get_or_create_dm_session
    from app.database import async_session

    async with async_session() as db:
        await _seed(db)
        assert (await get_or_create_dm_session(db, AI_USER, OWNER))["session_id"]
        assert (await get_or_create_dm_session(db, AI_USER, FRIEND))["session_id"]


async def test_human_can_open_a_dm_with_an_ai(migrated_db):
    """人找 AI：放行（AI 是公开可聊的），AI 之间的私信同样放行"""
    from app.chat.dm import get_or_create_dm_session
    from app.database import async_session

    async with async_session() as db:
        await _seed(db)
        assert (await get_or_create_dm_session(db, STRANGER, AI_USER))["session_id"]
        assert (await get_or_create_dm_session(db, AI_USER, AI_USER_2))["session_id"]


async def test_reply_inside_an_existing_session_is_not_blocked(migrated_db):
    """会话已经存在：AI 在里面回复不再看好友关系（QQ / 外部通道的被动回复靠这条）"""
    from app.chat.dm import get_or_create_dm_session, send_dm_message
    from app.database import async_session

    async with async_session() as db:
        await _seed(db)
        sid = (await get_or_create_dm_session(db, STRANGER, AI_USER))["session_id"]
        msg = await send_dm_message(db, sid, sender_id=AI_USER, content="在的")
        assert msg["content"] == "在的", msg


async def test_system_messages_are_never_blocked(migrated_db):
    """系统通知不拦：system 身份直接放行，显式 skip 也放行"""
    from app.chat.dm import get_or_create_dm_session
    from app.database import async_session

    async with async_session() as db:
        await _seed(db)
        assert (await get_or_create_dm_session(db, 0, STRANGER))["session_id"]
        assert (await get_or_create_dm_session(db, AI_USER, STRANGER, skip_friendship_check=True))["session_id"]


async def test_human_to_human_still_requires_friendship(migrated_db):
    """人 → 人：仍旧必须互为好友（这条没变）"""
    from app.chat.dm import get_or_create_dm_session
    from app.database import async_session

    async with async_session() as db:
        await _seed(db)
        msg = await _reject(lambda: get_or_create_dm_session(db, STRANGER, FRIEND))
        assert "请先添加好友" in msg, msg
