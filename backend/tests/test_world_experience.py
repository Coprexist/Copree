"""世界侧的跨情景经历：同一张表、另一种归属，投递标记记在会话元信息里

世界 AI 不是 agent（`world_ais` 独立表），所以经历归属用 `owner_type='world_ai'`。
世界侧每轮按 DB 重建 messages、没有账本，所以「投过没有」记在被投会话自己的
`world.config.sessions[key].experience_seen` 里：进过这个会话就不再投，换会话重新可投。
"""
import pytest

pytestmark = pytest.mark.anyio

WORLD_ID = 7


async def _seed(db):
    from sqlalchemy import text

    from db_reset import clear

    await clear(db, "agent_experiences", "world_ais", "worlds", "users")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES (1, '甲', 'x', 'human')"))
    await db.execute(text(
        "INSERT INTO worlds (id, name, owner_id, status, config) "
        "VALUES (7, '化学世界', 1, 'active', '{\"current_session\": \"s1\"}'::jsonb)"))


async def _repo(db):
    from app.repositories.world_repo import SQLAlchemyWorldRepository

    return SQLAlchemyWorldRepository(db)


async def test_world_turn_is_recorded_once_per_identical_summary(migrated_db):
    """同一场里重复调同一个工具不堆行；换了内容才写新的一行"""
    from sqlalchemy import select

    from app.database import async_session
    from app.models.agent_experience import AgentExperience
    from app.models.world import World
    from app.services.experience.world_experience import record_world_turn
    from app.services.world.world_service import ensure_world_ai

    async with async_session() as db:
        await _seed(db)
        repo = await _repo(db)
        world = await repo.get(World, WORLD_ID)
        wai = await ensure_world_ai(repo, WORLD_ID)

        await record_world_turn(repo, world, wai, tools_done=["file_write: 写了 index.html"])
        await record_world_turn(repo, world, wai, tools_done=["file_write: 写了 index.html"])
        rows = (await db.execute(select(AgentExperience))).scalars().all()
        assert len(rows) == 1, [r.summary for r in rows]
        assert rows[0].owner_type == "world_ai" and rows[0].owner_id == wai.id
        assert rows[0].scene_key == f"world:{WORLD_ID}:s1"

        await record_world_turn(repo, world, wai, tools_done=["execute: 跑了测试"])
        rows = (await db.execute(select(AgentExperience))).scalars().all()
        assert len(rows) == 2


async def test_world_recall_delivers_once_per_session(migrated_db):
    """别的会话里干过的事投进本会话一次；同一会话再相干也不再投"""
    from app.database import async_session
    from app.models.world import World
    from app.services.experience import experience_service
    from app.services.experience.world_experience import (
        OWNER_TYPE, deliver_world_experiences,
    )
    from app.services.world.world_service import ensure_world_ai

    async with async_session() as db:
        await _seed(db)
        repo = await _repo(db)
        world = await repo.get(World, WORLD_ID)
        wai = await ensure_world_ai(repo, WORLD_ID)

        await experience_service.record(
            db, owner_type=OWNER_TYPE, owner_id=wai.id,
            context_ref=f"world:{WORLD_ID}:old", summary="推导过配平方程，结论在第 3 步",
            label="化学世界 · 旧会话", source=experience_service.SOURCE_AI,
        )

        messages: list[dict] = []
        n = await deliver_world_experiences(
            repo, world, wai, messages, query="配平方程 怎么推", limit=5, fresh=False)
        assert n == 1 and "配平方程" in messages[0]["content"]
        assert "别的会话" in messages[0]["content"], "边界句要说一次"

        again: list[dict] = []
        assert await deliver_world_experiences(
            repo, world, wai, again, query="配平方程", limit=5, fresh=False) == 0
        assert again == []


async def test_world_no_match_delivers_nothing(migrated_db):
    """命不中就不投：不拿时间序硬凑"""
    from app.database import async_session
    from app.models.world import World
    from app.services.experience import experience_service
    from app.services.experience.world_experience import (
        OWNER_TYPE, deliver_world_experiences,
    )
    from app.services.world.world_service import ensure_world_ai

    async with async_session() as db:
        await _seed(db)
        repo = await _repo(db)
        world = await repo.get(World, WORLD_ID)
        wai = await ensure_world_ai(repo, WORLD_ID)
        await experience_service.record(
            db, owner_type=OWNER_TYPE, owner_id=wai.id,
            context_ref=f"world:{WORLD_ID}:old", summary="推导过配平方程",
            source=experience_service.SOURCE_AI,
        )

        messages: list[dict] = []
        assert await deliver_world_experiences(
            repo, world, wai, messages, query="今天天气怎么样", limit=5, fresh=False) == 0
        assert messages == []
