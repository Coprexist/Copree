"""窗口泵：出链之后，链上还有谁该跑

2026-11-26 口径（docs/dev/state_chain.md §5）：

* 窗口 = 链尾起 `max_active` 个节点，默认 1；
* 「谁该跑」由 `due_frames` 回答：窗口内、**有未读或没做完的轮**；
* 出一帧、起一帧：上一帧出链后下一帧进窗口，泵按它拉起，不需要等下一条消息。

额度只由刺激恢复（frame_turn_lifecycle §4.1）：泵是轮转，不调 `new_stimulus`。
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


async def _due(db):
    from app.services.agent.state_stack_service import due_frames

    return await due_frames(db, AI_ID)


def _refs(frames):
    return [f["context_ref"] for f in frames]


async def test_due_frames_empty_without_frames(migrated_db):
    """链上什么都没有：没有人该跑"""
    from app.database import async_session

    async with async_session() as db:
        await _seed(db)
        assert await _due(db) == []


async def test_window_full_means_nobody_due(migrated_db):
    """甲群在跑（窗口 1 被占）：乙群挂着也不算「该跑」——它排着队，不是轮到它"""
    from app.database import async_session
    from app.services.agent.state_stack_service import attach_stimulus, begin_turn

    async with async_session() as db:
        await _seed(db)
        await attach_stimulus(db, AI_ID, "group_chat", "group:79", "甲群", "某人")
        await begin_turn(db, AI_ID, "group:79", "turn-1")
        await attach_stimulus(db, AI_ID, "group_chat", "group:78", "乙群", "阿岚", summary="在吗")
        await db.commit()

        assert _refs(await _due(db)) == []


async def test_window_freed_hands_off_to_next(migrated_db):
    """甲群出链 → 乙群进窗口、有未读 → 该它跑了（出链即续跑，不用等新消息）"""
    from app.database import async_session
    from app.services.agent.state_stack_service import (
        attach_stimulus, begin_turn, end_turn_frame,
    )

    async with async_session() as db:
        await _seed(db)
        await attach_stimulus(db, AI_ID, "group_chat", "group:79", "甲群", "某人")
        await begin_turn(db, AI_ID, "group:79", "turn-1")
        await attach_stimulus(db, AI_ID, "group_chat", "group:78", "乙群", "阿岚", summary="在吗")
        await db.commit()

        assert await end_turn_frame(db, AI_ID, turn_id="turn-1") is True
        await db.commit()

        due = await _due(db)
        assert _refs(due) == ["group:78"]
        assert due[0]["turn_state"] == "open"       # 挂起等着，不是正在跑
        assert int(due[0]["unread"]) == 1


async def test_frame_in_window_without_unread_is_not_due(migrated_db):
    """窗口轮到它，但没未读、也没做过一半的轮：不为它烧一次调用"""
    import json

    from app.database import async_session
    from app.services.agent.state_stack_service import attach_stimulus

    async with async_session() as db:
        await _seed(db)
        await attach_stimulus(db, AI_ID, "group_chat", "group:79", "甲群", "某人")
        await db.commit()

        # 直接改帧内容，不经过任何业务路径：造出「在窗口、active、但无事可办」
        row = await db.execute(text("SELECT state_stack FROM agents WHERE id = 25"))
        stack = row.scalar_one()
        stack[0]["turn_state"] = "closed"
        stack[0]["unread"] = 0
        await db.execute(
            text("UPDATE agents SET state_stack = CAST(:s AS jsonb) WHERE id = 25"),
            {"s": json.dumps(stack)})
        await db.commit()

        assert await _due(db) == []
