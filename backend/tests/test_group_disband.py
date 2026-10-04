"""解散群聊：退场收尾（记忆改私有 + 改锚 + 清悬空引用）

为什么值得钉住：`rough_memories.group_id` 外键是 NO ACTION，不清它就删不掉群；
而"只把 group_id 置空"会留下 `scope='group' AND group_id IS NULL` 的行——召回的群记忆
按 group_id 匹配，那种行谁也召不回，等于数据在、再也想不起来。
"""
import pytest
from sqlalchemy import text

pytestmark = pytest.mark.anyio


async def _seed(db):
    from db_reset import clear

    await clear(db, "rough_memories", "detail_memories", "structured_records",
                "group_members", "groups", "agents", "users")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES "
        "(1, '群主', 'x', 'human'), (2, 'AI账号', 'x', 'ai')"))
    await db.execute(text(
        "INSERT INTO agents (id, owner_id, name, user_id, discoverable) "
        "VALUES (1, 1, '测试AI', 2, true)"))
    await db.execute(text(
        "INSERT INTO groups (id, name, owner_type, owner_id, avatar_mode, include_ai_in_avatar,"
        " searchable, auto_approve_join, approve_invites, name_from_channel) VALUES "
        "(77, '测试群', 'human', 1, 'default', true, false, true, false, false)"))
    await db.execute(text(
        "INSERT INTO group_members (group_id, member_type, member_id, role) "
        "VALUES (77, 'human', 1, 'owner')"))
    await db.commit()


async def _group_memory(db, mid: int, refs):
    import json

    await db.execute(text(
        "INSERT INTO rough_memories (id, owner_type, owner_id, title, scope, group_id,"
        " mem_type, value_score, session_refs, session_foci, semantic_foci) VALUES "
        "(:id, 'ai', 1, '群里的事', 'group', 77, 'daily', 3,"
        " CAST(:refs AS jsonb), '[]'::jsonb, '[]'::jsonb)"
    ), {"id": mid, "refs": json.dumps(refs)})
    await db.commit()


async def test_disband_keeps_memories_recallable(migrated_db):
    """群没了，事还在：记忆改成该 AI 私有，锚在死会话上的锚点改锚「所有聊天」"""
    from app.database import async_session
    from app.chat.gm import disband_group

    async with async_session() as db:
        await _seed(db)
        await _group_memory(db, 950, ["group:77"])

        await disband_group(db, 77, operator_id=1)
        await db.commit()

        row = (await db.execute(text(
            "SELECT scope, group_id, session_refs FROM rough_memories WHERE id = 950"))).one()
        assert row.scope == "private", "留成 group + group_id 为空 = 谁也召不回"
        assert row.group_id is None
        assert row.session_refs == ["all-chats"], "锚在死会话上 → 任何上下文都够不着，必须改锚"

        left = (await db.execute(text("SELECT count(*) FROM groups WHERE id = 77"))).scalar()
        assert left == 0


async def test_admin_disband_does_not_trip_the_foreign_key(migrated_db):
    """管理员强制解散曾直接删群：只要该群有群共享记忆就撞外键（NO ACTION）"""
    from app.database import async_session
    from app.chat.gm import disband_group

    async with async_session() as db:
        await _seed(db)
        await _group_memory(db, 951, [])

        await disband_group(db, 77, operator_id=None)   # 管理员路径：不校验群主
        await db.commit()

        assert (await db.execute(text("SELECT count(*) FROM groups WHERE id = 77"))).scalar() == 0


async def test_disband_clears_dangling_refs(migrated_db):
    """悬空引用一起清：焦段元素、闹钟来源"""
    from app.database import async_session
    from app.chat.gm import disband_group
    from app.services.agent import focus_service

    async with async_session() as db:
        await _seed(db)
        foci, focus, _ = await focus_service.create(db, 1, "学生群", "session")
        await focus_service.join_session(db, 1, focus["id"], "group:77")
        await db.execute(text(
            "INSERT INTO agent_alarms (agent_id, wake_at, task, origin_context_ref) "
            "VALUES (1, now() + interval '1 hour', '去群里说一声', 'group:77')"))
        await db.commit()

        await disband_group(db, 77, operator_id=1)
        await db.commit()

        foci = await focus_service.load(db, 1)
        joined = [f["elements"] for f in foci if f["id"] == focus["id"]]
        assert joined == [[]], "会话没了，焦段里不该留着它"
        alarm_ref = (await db.execute(text(
            "SELECT origin_context_ref FROM agent_alarms WHERE agent_id = 1"))).scalar()
        assert alarm_ref is None
