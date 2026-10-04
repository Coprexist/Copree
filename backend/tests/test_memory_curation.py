"""整理已有记忆：改（store_memory 带 memory_id）与删（forget_memory）

现状是"只写不修"：锚错了改不回来、记错了删不掉，只能再存一条，越存越乱。
改走写入侧同一个工具——字段形状、锚点物化、权值口径共用一份；
删单独一个工具——不可逆的事单独可见。两处都从 memory_service.owned_memory 取行，
所有权只判一次。
"""
import pytest
from sqlalchemy import text

pytestmark = pytest.mark.anyio


async def _seed(db):
    from db_reset import clear

    await clear(db, "detail_memories", "rough_memories", "structured_records", "agents", "users")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES "
        "(1, '甲', 'x', 'human'), (2, 'AI账号', 'x', 'ai')"))
    await db.execute(text(
        "INSERT INTO agents (id, owner_id, name, user_id, discoverable) "
        "VALUES (24, 1, '化学老师', 2, true), (25, 1, '别的AI', 2, true)"))
    await db.commit()


async def _mk_memory(db, agent_id=24, title="暗号", content="7788", **cols):
    """直接插一条已存在的记忆（改/删的对象）：写入侧是缓冲队列，测试要一条现成的行。"""
    fields = {
        "owner_type": "ai", "owner_id": agent_id, "title": title, "scope": "private",
        "status": "active", "value_score": 4, "mem_type": "promise",
        "session_refs": "[]", "session_foci": "[]", "semantic_foci": "[]",
    }
    fields.update(cols)
    row = (await db.execute(text(
        "INSERT INTO rough_memories (owner_type, owner_id, title, scope, status, value_score,"
        " mem_type, session_refs, session_foci, semantic_foci) VALUES"
        " (:owner_type, :owner_id, :title, :scope, :status, :value_score, :mem_type,"
        " CAST(:session_refs AS jsonb), CAST(:session_foci AS jsonb), CAST(:semantic_foci AS jsonb))"
        " RETURNING id"), fields)).scalar_one()
    await db.execute(text(
        "INSERT INTO detail_memories (rough_id, content) VALUES (:rid, :content)"),
        {"rid": row, "content": content})
    await db.commit()
    return row


async def _read(db, memory_id):
    row = (await db.execute(text(
        "SELECT rm.title, rm.value_score, rm.mem_type, rm.status, rm.scope,"
        " rm.session_refs, rm.session_foci, rm.semantic_foci, dm.content"
        " FROM rough_memories rm LEFT JOIN detail_memories dm ON dm.rough_id = rm.id"
        " WHERE rm.id = :id"), {"id": memory_id})).first()
    return row


async def test_update_rewrites_fields_and_reanchors(migrated_db):
    from app.database import async_session
    from app.services.agent import focus_service
    from app.tools.memory.store_memory import StoreMemory

    async with async_session() as db:
        await _seed(db)
        _, topic, _ = await focus_service.create(db, 24, "化学教学", "semantic")
        await db.commit()
        mid = await _mk_memory(db)

        out = await StoreMemory().execute(db, 24, None, {
            "memory_id": mid, "title": "暗号（改）", "content": "8899",
            "weight": 2, "mem_type": "daily", "semantic_foci": [topic["id"]],
        }, {"session_id": "40_90", "api_base_url": "https://api.deepseek.com"})

        assert out["success"] and out["id"] == mid, out
        assert set(out["changed"]) >= {"title", "content", "weight", "mem_type", "anchors"}, out
        row = await _read(db, mid)
        assert row.title == "暗号（改）" and row.content == "8899"
        assert row.value_score == 2 and row.mem_type == "daily"
        assert row.semantic_foci == [topic["id"]]
        # 两轴都没锚才物化"当前会话"（写入侧同一条规则）：只给语义轴时会话轴就是空的
        assert row.session_refs == []
        assert out["anchors"] == {"refs": [], "session": [], "semantic": [topic["id"]]}


async def test_update_with_no_anchors_at_all_narrows_to_this_session(migrated_db):
    """把两轴都清掉 = 最窄的一档：只在本会话 + 当前语义焦段可见（与新建那条路一个口径）"""
    from app.database import async_session
    from app.tools.memory.store_memory import StoreMemory

    async with async_session() as db:
        await _seed(db)
        mid = await _mk_memory(db, session_foci='["chem"]', semantic_foci='["topic"]')

        out = await StoreMemory().execute(db, 24, None, {
            "memory_id": mid, "session_foci": [], "semantic_foci": [],
        }, {"session_id": "40_90"})

        assert out["anchors"] == {"refs": ["40_90"], "session": [], "semantic": []}, out
        row = await _read(db, mid)
        assert row.session_foci == [] and row.semantic_foci == []
        assert row.session_refs == ["40_90"]


async def test_update_leaves_untouched_fields_alone(migrated_db):
    """只改权值：标题、正文、锚点一个都不许动（不传 = 不改，不是 = 清空）"""
    from app.database import async_session
    from app.tools.memory.store_memory import StoreMemory

    async with async_session() as db:
        await _seed(db)
        mid = await _mk_memory(db, session_foci='["chem"]', semantic_foci='["topic"]')

        out = await StoreMemory().execute(db, 24, None,
                                          {"memory_id": mid, "weight": 5}, {"session_id": "40_90"})

        assert out["changed"] == ["weight"], out
        row = await _read(db, mid)
        assert row.title == "暗号" and row.content == "7788"
        assert row.session_foci == ["chem"] and row.semantic_foci == ["topic"]


async def test_update_rejects_someone_elses_memory(migrated_db):
    from app.database import async_session
    from app.tools.memory.store_memory import StoreMemory

    async with async_session() as db:
        await _seed(db)
        mid = await _mk_memory(db, agent_id=25, title="别人的秘密")

        out = await StoreMemory().execute(db, 24, None,
                                          {"memory_id": mid, "weight": 1}, {"session_id": "40_90"})

        assert out.get("error") and "没找到" in out["message"], out
        assert (await _read(db, mid)).title == "别人的秘密", "越权改不动别人的记忆"


async def test_update_endorses_a_pending_archive_memory(migrated_db):
    """他亲自动手改过 = 认这条：待归档的转正，别让每日整理再把它收拾掉"""
    from app.database import async_session
    from app.tools.memory.store_memory import StoreMemory

    async with async_session() as db:
        await _seed(db)
        mid = await _mk_memory(db, status="pending_archive", value_score=1, mem_type="daily")

        out = await StoreMemory().execute(db, 24, None,
                                          {"memory_id": mid, "weight": 3}, {"session_id": "40_90"})

        assert "status" in out["changed"], out
        assert (await _read(db, mid)).status == "active"


async def test_forget_memory_removes_the_row_and_its_content(migrated_db):
    from app.database import async_session
    from app.tools.memory.forget_memory import ForgetMemory

    async with async_session() as db:
        await _seed(db)
        mid = await _mk_memory(db)

        out = await ForgetMemory().execute(db, 24, None,
                                           {"memory_id": mid, "reason": "重复了"}, {})

        assert out["success"] and out["title"] == "暗号", out
        assert await _read(db, mid) is None
        left = (await db.execute(text(
            "SELECT count(*) FROM detail_memories WHERE rough_id = :id"), {"id": mid})).scalar()
        assert left == 0, "正文随外键级联一起走，不留孤儿"


async def test_forget_memory_rejects_someone_elses_memory(migrated_db):
    from app.database import async_session
    from app.tools.memory.forget_memory import ForgetMemory

    async with async_session() as db:
        await _seed(db)
        mid = await _mk_memory(db, agent_id=25)

        out = await ForgetMemory().execute(db, 24, None, {"memory_id": mid}, {})

        assert out.get("error"), out
        assert await _read(db, mid) is not None


async def test_store_still_needs_the_fields_when_creating(migrated_db):
    """新建那条路照旧要标题+正文+范围+类型；两条路要的字段不同，判在 execute 里"""
    from app.database import async_session
    from app.tools.memory.store_memory import StoreMemory

    async with async_session() as db:
        await _seed(db)
        out = await StoreMemory().execute(db, 24, None, {"title": "只有标题"}, {"session_id": "40_90"})
        assert out.get("error") and "缺字段" in out["message"], out
