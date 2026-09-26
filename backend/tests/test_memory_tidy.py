"""记忆整理：低权值流水物理删除，待归档条目去重

见 docs/memory_system/design/focus_and_memory_reach.md 第七节。
"""
import pytest

pytestmark = pytest.mark.anyio


async def _seed(db):
    from sqlalchemy import text

    from db_reset import clear

    await clear(db, "detail_memories", "rough_memories", "agents", "users")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES "
        "(1, '甲', 'x', 'human'), (2, 'AI账号', 'x', 'ai')"))
    await db.execute(text(
        "INSERT INTO agents (id, owner_id, name, user_id, discoverable, state) "
        "VALUES (24, 1, '化学老师', 2, true, 'active')"))
    await db.commit()


async def _memory(db, mem_id, *, weight, days, status="active", title="流水"):
    from sqlalchemy import text

    await db.execute(text(
        "INSERT INTO rough_memories (id, owner_type, owner_id, title, scope, status,"
        " value_score, mem_type, last_touched_at, last_touched_call) VALUES"
        " (:i, 'ai', 24, :t, 'private', :s, :w, 'daily',"
        " now() - make_interval(days => :d), 0)"
    ), {"i": mem_id, "t": title, "s": status, "w": weight, "d": days})
    await db.commit()


async def test_tidy_deletes_only_the_lowest_band(migrated_db):
    from sqlalchemy import text

    from app.database import async_session
    from app.services.memory.tidy_service import tidy_agent_memories

    async with async_session() as db:
        await _seed(db)
        await _memory(db, 1, weight=1, days=400)   # 最低档且早已退场 → 删
        await _memory(db, 2, weight=5, days=400)   # 权值高，只是淡出 → 留
        await _memory(db, 3, weight=1, days=1)     # 最低档但还新鲜 → 留

        out = await tidy_agent_memories(db, 24)
        await db.commit()

        left = [r[0] for r in (await db.execute(text(
            "SELECT id FROM rough_memories ORDER BY id"))).all()]

        assert out["deleted"] == 1 and left == [2, 3], (out, left)


async def test_tidy_merges_duplicate_pending_entries(migrated_db):
    from sqlalchemy import text

    from app.database import async_session
    from app.services.memory.tidy_service import tidy_agent_memories

    async with async_session() as db:
        await _seed(db)
        await _memory(db, 1, weight=5, days=0, status="pending_archive", title="同名流水")
        await _memory(db, 2, weight=5, days=0, status="pending_archive", title="同名流水")
        await _memory(db, 3, weight=5, days=0, status="pending_archive", title="独特的一条")

        out = await tidy_agent_memories(db, 24)
        await db.commit()

        rows = (await db.execute(text(
            "SELECT id, status FROM rough_memories ORDER BY id"))).all()

        assert out["merged"] == 1 and out["promoted"] == 2, out
        assert [r[0] for r in rows] == [1, 3], rows
        assert all(r[1] == "active" for r in rows), rows
