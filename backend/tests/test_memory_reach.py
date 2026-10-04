"""焦段锚点参与召回：够不着的丢掉，命中多的排前面

设计见 docs/memory_system/design/focus_and_memory_reach.md §六（空集语义）、§九（检索）。
修前的状态是"锚点只写不读"——「换个地方就找不到」只是工具里的一句警告，实际处处可见，
等于隐式全局，与 §六 明令相悖。
"""
import pytest
from sqlalchemy import text

pytestmark = pytest.mark.anyio


async def _seed(db):
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES "
        "(1, '测试用户', 'x', 'human'), (2, '测试AI账号', 'x', 'ai') "
        "ON CONFLICT (id) DO NOTHING"
    ))
    await db.execute(text(
        "INSERT INTO agents (id, owner_id, name, user_id, discoverable, foci) "
        "VALUES (1, 1, '测试AI', 2, true, '[]'::jsonb) "
        "ON CONFLICT (id) DO UPDATE SET foci = '[]'::jsonb"
    ))
    await db.execute(text("DELETE FROM rough_memories WHERE owner_type = 'ai' AND owner_id = 1"))
    await db.commit()


async def _memory(db, mid: int, title: str, *, refs=None, s_foci=None, m_foci=None):
    await db.execute(text(
        "INSERT INTO rough_memories (id, owner_type, owner_id, title, scope, mem_type, value_score,"
        " session_refs, session_foci, semantic_foci) VALUES "
        "(:id, 'ai', 1, :title, 'private', 'daily', 3, "
        " CAST(:refs AS jsonb), CAST(:s AS jsonb), CAST(:m AS jsonb))"
    ), {"id": mid, "title": title,
        "refs": __import__("json").dumps(refs or []),
        "s": __import__("json").dumps(s_foci or []),
        "m": __import__("json").dumps(m_foci or [])})


async def test_empty_anchors_are_materialised_to_the_current_context(migrated_db):
    """空集必须物化：空集落库后读侧无从知道"本会话"是哪一个，那条记忆就成了处处可见"""
    from app.database import async_session
    from app.services.agent import focus_service

    async with async_session() as db:
        await _seed(db)
        refs, s_foci, m_foci, notes = await focus_service.resolve_anchors(
            db, 1, 64, {}, None, None)
        assert refs == ["group:64"], "空集 → 物化成当前会话"
        assert s_foci == [] and m_foci == [] and notes == []

        # 私信：会话键就是 session_id
        refs, _, _, _ = await focus_service.resolve_anchors(
            db, 1, None, {"session_id": "12_106"}, None, None)
        assert refs == ["12_106"]


async def test_explicit_focus_anchor_is_left_alone(migrated_db):
    """给了焦段锚点就不物化——它已经表达了适用范围"""
    from app.database import async_session
    from app.services.agent import focus_service

    async with async_session() as db:
        await _seed(db)
        foci, focus, _ = await focus_service.create(db, 1, "学生群", "session")

        refs, s_foci, _, _ = await focus_service.resolve_anchors(
            db, 1, 64, {}, [focus["id"]], None)
        assert refs == [] and s_foci == [focus["id"]]


async def test_recall_drops_what_is_anchored_elsewhere_and_ranks_by_hits(migrated_db):
    """够不着的丢掉；命中元素多的排前面；同命中数保持相关性顺序"""
    from app.database import async_session
    from app.services.memory.memory_service import _apply_reach

    async with async_session() as db:
        await _seed(db)
        await _memory(db, 901, "别处的记忆", refs=["group:99"])
        await _memory(db, 902, "这里的记忆", refs=["group:2"])
        await _memory(db, 903, "全局的记忆", refs=["group:2"], s_foci=["all-chats"])
        await db.commit()

        out = await _apply_reach(
            db, 1, [{"id": 901}, {"id": 902}, {"id": 903}], "group:2", "")
        ids = [m["id"] for m in out]
        assert ids == [903, 902], f"命中多的排前、锚在别处的丢掉，实际 {ids}"
        assert out[0]["reach"] == 2 and out[1]["reach"] == 1


async def test_recall_keeps_empty_anchor_rows(migrated_db):
    """存量空锚点按"够得着"处理：修这个 bug 不该让历史记忆集体消失"""
    from app.database import async_session
    from app.services.memory.memory_service import _apply_reach

    async with async_session() as db:
        await _seed(db)
        await _memory(db, 904, "存量记忆")
        await db.commit()

        out = await _apply_reach(db, 1, [{"id": 904}], "group:2", "")
        assert [m["id"] for m in out] == [904]
