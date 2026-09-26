"""记忆写入带上类型、权值与焦段锚点

设计见 docs/memory_system/design/focus_and_memory_reach.md 第七、八节。
空集不是全局：不锚任何焦段时只在本会话与当前语义焦段下可见，工具必须说清楚。
"""
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


def test_empty_anchor_set_is_the_narrowest_band():
    from app.utils.pure.focus import anchor_warning, normalize_anchor_ids

    assert normalize_anchor_ids([" a ", "a", "", None, "b"]) == ["a", "b"]
    assert "未锚定焦段" in anchor_warning([], [])
    assert anchor_warning(["all-chats"], []) == "", "锚了就不再提醒"
    assert anchor_warning([], ["chem"]) == ""


async def test_store_memory_reports_weight_and_rejects_unknown_anchor(migrated_db):
    from app.database import async_session
    from app.services.agent import focus_service
    from app.tools.memory.store_memory import StoreMemory

    async with async_session() as db:
        await _seed(db)
        _, topic, _ = await focus_service.create(db, 24, "化学教学", "semantic")
        await db.commit()

        out = await StoreMemory().execute(db, 24, None, {
            "title": "暗号", "content": "7788", "scope": "private",
            "mem_type": "promise", "semantic_foci": [topic["id"], "不存在的焦段"],
        }, {"session_id": "40_90"})

        assert out["success"] and out["weight"] == 4, out
        assert out["anchors"]["semantic"] == [topic["id"]], out
        assert "不存在" in out["message"], out
        assert "未锚定" not in out["message"], "锚了就不该再提醒"


async def test_store_memory_warns_when_nothing_is_anchored(migrated_db):
    from app.database import async_session
    from app.tools.memory.store_memory import StoreMemory

    async with async_session() as db:
        await _seed(db)
        out = await StoreMemory().execute(db, 24, None, {
            "title": "随手记", "content": "他今天穿了蓝衣服", "scope": "private",
            "mem_type": "daily",
        }, {"session_id": "40_90"})

        assert out["weight"] == 1, out
        assert out["anchors"] == {"session": [], "semantic": []}, out
        assert "未锚定焦段" in out["message"], out


async def test_batch_write_persists_type_weight_and_anchors(migrated_db):
    from sqlalchemy import text

    from app.database import async_session
    from app.services.memory.memory_buffer import PendingMemory, _batch_write_memories

    async with async_session() as db:
        await _seed(db)
        await _batch_write_memories(db, [PendingMemory(
            agent_id=24, group_id=None, title="化学小测范围", content="第三章",
            scope="private", api_base_url="http://127.0.0.1:1", api_key="x",
            trigger_user_id=None, mem_type="event", weight=4,
            session_foci=["all-chats"], semantic_foci=["chem"],
        )])
        await db.commit()

        row = (await db.execute(text(
            "SELECT mem_type, value_score, session_foci, semantic_foci "
            "FROM rough_memories"))).first()

        assert row[0] == "event" and row[1] == 4, row
        assert row[2] == ["all-chats"] and row[3] == ["chem"], row


async def test_manage_records_carries_anchors(migrated_db):
    from sqlalchemy import text

    from app.database import async_session
    from app.services.agent import focus_service
    from app.tools.memory.manage_records import ManageRecords

    async with async_session() as db:
        await _seed(db)
        _, group, _ = await focus_service.create(db, 24, "学生群", "session")
        await db.commit()

        out = await ManageRecords().execute(db, 24, None, {
            "action": "set", "category": "student_profile", "sub_key": "1",
            "field": "有机化学", "value": "掌握了苯密度",
            "mem_type": "event", "weight": 3, "session_foci": [group["id"]],
        }, {"session_id": "40_90"})
        assert out["success"], out

        row = (await db.execute(text(
            "SELECT mem_type, value_score, session_foci FROM structured_records"))).first()

        assert row[0] == "event" and row[1] == 3 and row[2] == [group["id"]], row


def test_over_limit_only_warns_and_never_truncates():
    from app.utils.pure.memory_shape import (
        MAX_CONTENT_CHARS, MAX_TITLE_CHARS, over_limit, shape_warnings)

    assert over_limit("标题", "短标题", MAX_TITLE_CHARS) == ""
    warn = over_limit("标题", "长" * (MAX_TITLE_CHARS + 5), MAX_TITLE_CHARS)
    assert "超过" in warn and str(MAX_TITLE_CHARS) in warn, warn
    assert "原样存下" in warn, "要说清平台没有替它改字"
    assert len(shape_warnings("长" * 50, "内" * (MAX_CONTENT_CHARS + 1))) == 2


async def test_store_memory_warns_but_keeps_the_text(migrated_db):
    """超限只提醒、不动内容：这条仍然按 AI 写的原文存下"""
    from app.database import async_session
    from app.tools.memory.store_memory import StoreMemory

    long_title = "标题" * 20          # 40 字，超过 30 字上限
    async with async_session() as db:
        await _seed(db)
        out = await StoreMemory().execute(db, 24, None, {
            "title": long_title, "content": "要点", "scope": "private",
            "mem_type": "event",
        }, {"session_id": "40_90"})

        assert out["title"] == long_title, "平台不裁剪 AI 写的内容"
        assert "超过" in out.get("message", "") and "30 字" in out["message"], out

