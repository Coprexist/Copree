"""刺激到达：把帧摆进状态链并记未读，去不去由 AI 自己定

系统不替 AI 切帧（docs/dev/frame_turn_lifecycle.md 刺激传导）。别的会话里有人找他时，
那一帧挂上链、记未读，当前帧原样不动；他决定过去时 pull 那一帧，未读清零、额度按刺激重置。
"""
import pytest
from sqlalchemy import text

pytestmark = pytest.mark.anyio

AI_ID, AI_USER_ID = 24, 40


async def _seed(db):
    from db_reset import clear

    await clear(db, "group_members", "groups", "agents", "users")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES "
        "(1, '主人', 'x', 'human'), (40, '甲AI', 'x', 'ai')"))
    await db.execute(text(
        "INSERT INTO agents (id, owner_id, name, user_id, discoverable, state_stack) VALUES "
        "(24, 1, '甲AI', 40, true, '[]'::jsonb)"))
    for gid, name in ((59, "甲群"), (58, "乙群")):
        await db.execute(text(
            "INSERT INTO groups (id, name, owner_type, owner_id, avatar_mode, include_ai_in_avatar, "
            "searchable, auto_approve_join, approve_invites) VALUES "
            "(:id, :name, 'human', 1, 'default', true, true, true, true)"),
            {"id": gid, "name": name})
        await db.execute(text(
            "INSERT INTO group_members (group_id, member_type, member_id, role) VALUES "
            "(:id, 'ai', 40, 'member')"), {"id": gid})
    await db.commit()


async def _frames(db):
    from app.services.agent.state_stack_service import get_frames
    return await get_frames(db, AI_ID)


async def test_another_conversation_is_parked_not_switched(migrated_db):
    """乙群有人找：乙群的帧上链（挂起 + 未读），当前帧仍是甲群——不替他切帧"""
    from app.database import async_session
    from app.services.agent.state_stack_service import (
        attach_stimulus, get_current_frame,
    )

    async with async_session() as db:
        await _seed(db)
        await attach_stimulus(db, AI_ID, "group_chat", "group:59", "甲群", "某人", summary="在吗")
        await db.commit()
        before = await get_current_frame(db, AI_ID)
        assert before["context_ref"] == "group:59"

        await attach_stimulus(db, AI_ID, "group_chat", "group:58", "乙群", "阿岚", summary="晚上回吗")
        await db.commit()

        after = await get_current_frame(db, AI_ID)
        assert after["id"] == before["id"], "当前帧不该被顶掉"
        assert after["context_ref"] == "group:59"
        parked = [f for f in await _frames(db) if f["context_ref"] == "group:58"]
        assert len(parked) == 1
        assert parked[0]["status"] == "suspended"
        assert parked[0]["unread"] == 1
        assert parked[0]["last_stimulus"]["actor"] == "阿岚"


async def test_repeated_messages_accumulate_unread(migrated_db):
    """同一个人连发：帧不重复建，未读累加"""
    from app.database import async_session
    from app.services.agent.state_stack_service import attach_stimulus

    async with async_session() as db:
        await _seed(db)
        await attach_stimulus(db, AI_ID, "group_chat", "group:59", "甲群", "某人")
        await attach_stimulus(db, AI_ID, "group_chat", "group:58", "乙群", "阿岚", summary="在吗")
        await attach_stimulus(db, AI_ID, "group_chat", "group:58", "乙群", "阿岚", summary="人呢")
        await db.commit()

        parked = [f for f in await _frames(db) if f["context_ref"] == "group:58"]
        assert len(parked) == 1
        assert parked[0]["unread"] == 2
        assert parked[0]["last_stimulus"]["summary"] == "人呢"


async def test_the_summary_tells_him_someone_is_waiting(migrated_db):
    """摘要里能看见「谁在等你、有几条」——这就是告知，不额外烧一轮调用"""
    from app.database import async_session
    from app.services.agent.state_stack_service import (
        attach_stimulus, get_state_stack_summary,
    )

    async with async_session() as db:
        await _seed(db)
        await attach_stimulus(db, AI_ID, "group_chat", "group:59", "甲群", "某人")
        await attach_stimulus(db, AI_ID, "group_chat", "group:58", "乙群", "阿岚", summary="在吗")
        await db.commit()

        summary = await get_state_stack_summary(db, AI_ID)
        assert "乙群" in summary
        assert "1 条未读" in summary
        assert "pop_state" in summary


async def test_pulling_that_frame_adopts_the_stimulus(migrated_db):
    """他决定过去：pull 那一帧 → 未读清零，额度按这次刺激重置（§4.1）"""
    from app.database import async_session
    from app.services.agent.state_stack_service import (
        attach_stimulus, get_current_frame, pop_state,
    )

    async with async_session() as db:
        await _seed(db)
        await attach_stimulus(db, AI_ID, "group_chat", "group:59", "甲群", "某人")
        await attach_stimulus(db, AI_ID, "group_chat", "group:58", "乙群", "阿岚", summary="在吗")
        await db.commit()

        parked = [f for f in await _frames(db) if f["context_ref"] == "group:58"][0]
        await pop_state(db, AI_ID, target_frame_id=parked["id"])
        await db.commit()

        current = await get_current_frame(db, AI_ID)
        assert current["context_ref"] == "group:58"
        assert int(current.get("unread") or 0) == 0
        assert current["steps_used"] == 0
        assert current["steps_limit"] > 0
        assert current["turn_state"] == "running"


async def test_his_own_conversation_is_not_a_stimulus(migrated_db):
    """消息就落在当前这一帧：本来就该在这一轮处理，不记未读、不动帧"""
    from app.database import async_session
    from app.services.agent.state_stack_service import (
        attach_stimulus, get_current_frame,
    )

    async with async_session() as db:
        await _seed(db)
        await attach_stimulus(db, AI_ID, "group_chat", "group:59", "甲群", "某人")
        await db.commit()
        before = await get_current_frame(db, AI_ID)

        await attach_stimulus(db, AI_ID, "group_chat", "group:59", "甲群", "某人", summary="再说一句")
        await db.commit()

        after = await get_current_frame(db, AI_ID)
        assert after["id"] == before["id"]
        assert int(after.get("unread") or 0) == 0
