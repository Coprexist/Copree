"""轮次与帧的生死：开场认领，收尾归位

2026-11-23 外部反馈：正常一轮结束只扣步数、不关帧，turn_state 停在 running →
`attach_stimulus` 判「窗口被占」，只挂帧记未读、不唤醒——
表现就是「群里说完一轮，私信永远不回」（实测必现）。

口径：
  * 开场 `begin_turn(context_ref, turn_id)` 认领「这一轮演的是哪一帧」，把 turn_id 写上去——
    免费轮（提醒/收尾）一步都不扣，靠扣步打标识就漏了它们；
  * 收尾 `end_turn_frame(turn_id=…)` 按同一个标识找回来，走唯一出口 end_frames。

不按栈顶找：一个 AI 会在几个会话之间被切来切去，栈顶帧未必是这一轮演的那一帧。
"""
import pytest
from sqlalchemy import text

pytestmark = pytest.mark.anyio

AI_ID, AI_USER_ID = 25, 41


async def _seed(db):
    from db_reset import clear

    await clear(db, "group_members", "groups", "agents", "users")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES "
        "(1, '主人', 'x', 'human'), (41, '甲AI', 'x', 'ai')"))
    await db.execute(text(
        "INSERT INTO agents (id, owner_id, name, user_id, discoverable, state_stack) VALUES "
        "(25, 1, '甲AI', 41, true, '[]'::jsonb)"))
    for gid, name in ((79, "甲群"), (78, "乙群")):
        await db.execute(text(
            "INSERT INTO groups (id, name, owner_type, owner_id, avatar_mode, include_ai_in_avatar, "
            "searchable, auto_approve_join, approve_invites) VALUES "
            "(:id, :name, 'human', 1, 'default', true, true, true, true)"),
            {"id": gid, "name": name})
        await db.execute(text(
            "INSERT INTO group_members (group_id, member_type, member_id, role) VALUES "
            "(:id, 'ai', 41, 'member')"), {"id": gid})
    await db.commit()


async def _frames(db):
    from app.services.agent.state_stack_service import get_frames

    return await get_frames(db, AI_ID)


async def test_end_turn_lets_other_conversation_wake(migrated_db):
    """甲群说完一轮 → 乙群的新刺激能直接唤醒，而不是「窗口被占」"""
    from app.database import async_session
    from app.services.agent.state_stack_service import (
        attach_stimulus, begin_turn, end_turn_frame, get_current_frame,
    )

    async with async_session() as db:
        await _seed(db)
        await attach_stimulus(db, AI_ID, "group_chat", "group:79", "甲群", "某人")
        await db.commit()
        claimed = await begin_turn(db, AI_ID, "group:79", "turn-1")
        await db.commit()
        assert claimed["turn_id"] == "turn-1"
        assert (await get_current_frame(db, AI_ID))["turn_state"] == "running"

        # 甲群还在跑：乙群只能挂帧记未读
        await attach_stimulus(db, AI_ID, "group_chat", "group:78", "乙群", "阿岚", summary="在吗")
        await db.commit()
        parked = [f for f in await _frames(db) if f["context_ref"] == "group:78"]
        assert parked and parked[0]["status"] == "suspended"

        # 甲群这一轮结束：按 turn_id 归位，只关它自己
        assert await end_turn_frame(db, AI_ID, turn_id="turn-1") is True
        await db.commit()
        frame = [f for f in await _frames(db) if f["context_ref"] == "group:79"][0]
        assert frame["status"] == "ended"
        assert frame["ended_code"] == "done_by_ai"
        assert frame["steps_left"] == 0

        # 归位后乙群再来消息：不是「窗口被占」，而是直接 active + 认领未读
        await attach_stimulus(db, AI_ID, "group_chat", "group:78", "乙群", "阿岚", summary="人呢")
        await db.commit()
        woken = [f for f in await _frames(db) if f["context_ref"] == "group:78"][0]
        assert woken["status"] == "active"
        assert woken["unread"] == 0


async def test_unknown_turn_id_never_closes_another_conversation(migrated_db):
    """给了 turn_id 却找不到帧：说明这一轮的帧已经不在了，不许拿栈顶凑数"""
    from app.database import async_session
    from app.services.agent.state_stack_service import (
        attach_stimulus, end_turn_frame, get_current_frame,
    )

    async with async_session() as db:
        await _seed(db)
        await attach_stimulus(db, AI_ID, "group_chat", "group:79", "甲群", "某人")
        await db.commit()

        assert await end_turn_frame(db, AI_ID, turn_id="turn-404") is False
        await db.commit()

        still = await get_current_frame(db, AI_ID)
        assert still["context_ref"] == "group:79"
        assert still["turn_state"] == "running"


async def test_no_turn_id_falls_back_to_conversation(migrated_db):
    """老路径没有 turn_id 时退回会话键——兜底要留着，但它只在没有标识时生效"""
    from app.database import async_session
    from app.services.agent.state_stack_service import (
        attach_stimulus, end_turn_frame, get_current_frame,
    )

    async with async_session() as db:
        await _seed(db)
        await attach_stimulus(db, AI_ID, "group_chat", "group:79", "甲群", "某人")
        await db.commit()

        assert await end_turn_frame(db, AI_ID, context_ref="group:79") is True
        await db.commit()
        assert (await get_current_frame(db, AI_ID)) is None
