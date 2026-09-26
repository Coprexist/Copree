"""记忆召回的权值过滤与时间基准（2026-09-26）

设计见 docs/memory_system/design/focus_and_memory_reach.md 第七节：
- 设定权值 1-5 不变，时间权值按「最近一次被召回」现算、不落库；
- 自动注入只收还在有效期的条目；
- 被想起过就刷新时间基准——设定权值一个字不动。
"""
import pytest

pytestmark = pytest.mark.anyio

# 本地一个必然连不上的 embedding 端点：向量补位快速降级，用例只验关键词路径
_UNREACHABLE = "http://127.0.0.1:1"


async def _seed(db):
    """三条记忆：早已退场的流水、仍在有效期的核心、刚写入的流水。"""
    from sqlalchemy import text

    from db_reset import clear

    await clear(db, "detail_memories", "rough_memories", "users")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES (1, '甲', 'x', 'human')"
    ))
    # (id, 标题, 设定权值, 类型, 距最近一次被召回的天数)
    rows = [
        (1, "化学小测范围", 1, "daily", 400),
        (2, "化学老师偏好", 5, "person", 100),
        (3, "化学作业记录", 1, "daily", None),
    ]
    for rid, title, weight, mem_type, days in rows:
        if days is None:
            await db.execute(text(
                "INSERT INTO rough_memories (id, owner_type, owner_id, title, scope, status,"
                " value_score, mem_type) VALUES (:i, 'ai', 24, :t, 'private', 'active', :w, :m)"
            ), {"i": rid, "t": title, "w": weight, "m": mem_type})
        else:
            await db.execute(text(
                "INSERT INTO rough_memories (id, owner_type, owner_id, title, scope, status,"
                " value_score, mem_type, last_touched_at, last_touched_call) VALUES"
                " (:i, 'ai', 24, :t, 'private', 'active', :w, :m,"
                " now() - make_interval(days => :d), 0)"
            ), {"i": rid, "t": title, "w": weight, "m": mem_type, "d": days})
        await db.execute(text(
            "INSERT INTO detail_memories (rough_id, content) VALUES (:i, :c)"
        ), {"i": rid, "c": f"{title}的详细内容"})
    await db.commit()


async def test_auto_injection_drops_the_expired_flow(migrated_db):
    """静默 400 天的流水记忆不再注入；核心记忆与刚写入的流水仍进上下文"""
    from app.database import async_session
    from app.services.memory.memory_service import recall_relevant_memories

    async with async_session() as db:
        await _seed(db)
        got = await recall_relevant_memories(
            db, 24, query="化学", api_base_url=_UNREACHABLE, api_key="x")

        assert sorted(m["id"] for m in got) == [2, 3], got


async def test_explicit_recall_still_sees_everything(migrated_db):
    """显式检索不做权值过滤：退场的记忆记录还在，能取回来"""
    from app.database import async_session
    from app.services.memory.memory_service import recall_relevant_memories

    async with async_session() as db:
        await _seed(db)
        got = await recall_relevant_memories(
            db, 24, query="化学", api_base_url=_UNREACHABLE, api_key="x",
            only_injectable=False)

        assert sorted(m["id"] for m in got) == [1, 2, 3], got


async def test_recalled_memory_resets_its_clock(migrated_db):
    """被想起过就把基准推到当下；没被想起的保持原样（设定权值一个字不动）"""
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import text

    from app.database import async_session
    from app.services.memory.memory_service import recall_relevant_memories

    async with async_session() as db:
        await _seed(db)
        await recall_relevant_memories(
            db, 24, query="化学", api_base_url=_UNREACHABLE, api_key="x")

        rows = (await db.execute(text(
            "SELECT id, last_touched_at, value_score FROM rough_memories ORDER BY id"))).all()
        touched = {r[0]: r[1] for r in rows}
        weights = {r[0]: r[2] for r in rows}

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        assert touched[2] >= now - timedelta(minutes=5), "被想起过就要重新计时"
        assert touched[1] < now - timedelta(days=300), "没被想起的不动"
        assert weights == {1: 1, 2: 5, 3: 1}, "刷新基准不许动设定权值"
