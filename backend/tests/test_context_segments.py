"""上下文压缩段（context_segments）的契约守卫。

背景：压缩只留一句「中间 N 条已折叠」，而提示里指向的 expand_context 工具
根本不存在（2026-11-25 排查确认）。补工具的前提是**原文落在库里**，
且只有本人能读。
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.anyio


async def _seed_agents(db, *agent_ids: int):
    from sqlalchemy import text
    # owner 必须是真实 user（agents.owner_id 有外键）
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES (1, 'owner', 'x', 'human')"
        " ON CONFLICT (id) DO NOTHING"))
    users = ",".join(f"({i}, 'u{i}', 'x', 'ai')" for i in agent_ids)
    await db.execute(text(
        f"INSERT INTO users (id, username, password_hash, type) VALUES {users}"
        " ON CONFLICT (id) DO NOTHING"))
    agents = ",".join(f"({i}, 1, 'a{i}', {i}, true)" for i in agent_ids)
    await db.execute(text(
        f"INSERT INTO agents (id, owner_id, name, user_id, discoverable) VALUES {agents}"
        " ON CONFLICT (id) DO NOTHING"))
    await db.commit()


def _msgs(n: int = 3) -> list[dict]:
    return [{"role": "user" if i % 2 == 0 else "assistant", "content": f"第{i}句"}
            for i in range(n)]


async def test_save_then_load_round_trip(migrated_db):
    from app.database import async_session
    from app.services.history import context_segment_service as svc

    async with async_session() as db:
        await _seed_agents(db, 901)
        seg_id = svc.new_segment_id()
        await svc.save(db, segment_id=seg_id, agent_id=901,
                       context_ref="group:1", messages=_msgs(5))
        await db.commit()

        loaded = await svc.load(db, agent_id=901, segment_id=seg_id)
        assert loaded is not None
        assert loaded["message_count"] == 5
        assert loaded["messages"][-1]["content"] == "第4句"


async def test_other_agent_cannot_read(migrated_db):
    """段号很短，不校验归属就等于开了个可枚举的读接口。"""
    from app.database import async_session
    from app.services.history import context_segment_service as svc

    async with async_session() as db:
        await _seed_agents(db, 902, 903)
        seg_id = svc.new_segment_id()
        await svc.save(db, segment_id=seg_id, agent_id=902,
                       context_ref="group:1", messages=_msgs())
        await db.commit()

        assert await svc.load(db, agent_id=903, segment_id=seg_id) is None
        assert await svc.load(db, agent_id=902, segment_id=seg_id) is not None


async def test_purge_removes_only_expired(migrated_db):
    from datetime import datetime, timedelta

    from sqlalchemy import update

    from app.database import async_session
    from app.models.context_segment import ContextSegment
    from app.services.history import context_segment_service as svc

    async with async_session() as db:
        await _seed_agents(db, 904)
        fresh, old = svc.new_segment_id(), svc.new_segment_id()
        await svc.save(db, segment_id=fresh, agent_id=904, context_ref="group:1", messages=_msgs(1))
        await svc.save(db, segment_id=old, agent_id=904, context_ref="group:1", messages=_msgs(1))
        await db.execute(
            update(ContextSegment).where(ContextSegment.id == old)
            .values(expires_at=datetime.now() - timedelta(days=1)))
        await db.commit()

        removed = await svc.purge_expired(db)
        assert removed >= 1
        assert await svc.load(db, agent_id=904, segment_id=fresh) is not None
        assert await svc.load(db, agent_id=904, segment_id=old) is None
