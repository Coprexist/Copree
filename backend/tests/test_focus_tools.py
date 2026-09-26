"""焦段的工具与摘要：显式归入、每轮复述、合并前先看记忆

设计见 docs/memory_system/design/focus_and_memory_reach.md 第四、五、十节。
"""
import json

import pytest

pytestmark = pytest.mark.anyio


async def _seed(db):
    from sqlalchemy import text

    from db_reset import clear

    await clear(db, "detail_memories", "rough_memories", "structured_records", "agents", "users")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES "
        "(1, '甲', 'x', 'human'), (2, 'AI账号', 'x', 'ai')"))
    await db.execute(text(
        "INSERT INTO agents (id, owner_id, name, user_id, discoverable) "
        "VALUES (24, 1, '化学老师', 2, true)"))
    await db.commit()


async def test_join_makes_the_session_visible_to_that_focus(migrated_db):
    """归入之前只算当前会话；归入之后这个会话才算那一类的一员"""
    from app.database import async_session
    from app.tools.self_management.list_focus import ListFocus
    from app.tools.self_management.switch_focus import SwitchFocus

    async with async_session() as db:
        await _seed(db)
        ctx = {"session_id": "40_90"}
        switch = SwitchFocus()

        out = await switch.execute(db, 24, None,
                                   {"action": "create", "name": "学生群", "axis": "session"}, ctx)
        assert out["success"] and "学生群" in out["message"], out
        fid = next(f["id"] for f in out["focuses"] if f["name"] == "学生群")
        assert {f["name"]: f["current"] for f in out["focuses"]}["学生群"] is False

        out = await switch.execute(db, 24, None, {"action": "join", "focus_id": fid}, ctx)
        assert out["success"] and "归入" in out["message"], out

        listed = await ListFocus().execute(db, 24, None, {}, ctx)
        current = {f["name"]: f["current"] for f in listed["focuses"]}
        assert current["学生群"] is True and current["所有聊天"] is True


async def test_state_summary_repeats_where_this_session_belongs(migrated_db):
    """摘要每轮复述归属：两条轴都要念出来"""
    from app.database import async_session
    from app.services.agent import focus_service
    from app.services.agent import state_stack_service as svc
    from app.utils.pure.state_stack import make_state_frame

    async with async_session() as db:
        await _seed(db)
        await svc.push_state(db, 24, make_state_frame(
            type_="dm", context_ref="40_90", why="收到消息", doing="回复对方"))
        _, group, _ = await focus_service.create(db, 24, "学生群", "session")
        _, topic, _ = await focus_service.create(db, 24, "化学教学", "semantic")
        await focus_service.join_session(db, 24, group["id"], "40_90")
        await svc.set_active_semantic_focus(db, 24, topic["id"])
        await db.commit()

        summary = await svc.get_state_stack_summary(db, 24)

        assert "焦段: " in summary and "学生群" in summary, summary
        assert "所有聊天" in summary, "预置焦段也要在归属里"
        assert "语义焦段：化学教学" in summary, summary


async def test_merge_shows_memories_then_repoints_them(migrated_db):
    """合并是两步：先看清两边，确认后才合并，且记忆锚点一起改指"""
    from sqlalchemy import text

    from app.database import async_session
    from app.services.agent import focus_service
    from app.tools.self_management.merge_focus import MergeFocus

    async with async_session() as db:
        await _seed(db)
        _, keep, _ = await focus_service.create(db, 24, "学生群", "session")
        _, drop, _ = await focus_service.create(db, 24, "补课群", "session")
        await db.execute(text(
            "INSERT INTO rough_memories (id, owner_type, owner_id, title, scope, status,"
            " value_score, mem_type, session_foci) VALUES"
            " (1, 'ai', 24, '化学小测', 'private', 'active', 3, 'event', CAST(:foci AS jsonb))"
        ), {"foci": json.dumps([drop["id"]])})
        await db.commit()

        merge = MergeFocus()
        args = {"keep_id": keep["id"], "drop_id": drop["id"]}

        preview = await merge.execute(db, 24, None, dict(args), {})
        assert preview["need_confirm"] is True, preview
        assert [m["title"] for m in preview["drop"]] == ["化学小测"], preview
        assert preview["keep"] == []

        out = await merge.execute(db, 24, None, {**args, "confirm": True}, {})
        assert out["success"] and "锚点已改指" in out["message"], out

        anchors = (await db.execute(text(
            "SELECT session_foci FROM rough_memories WHERE id=1"))).first()[0]
        assert anchors == [keep["id"]], anchors
