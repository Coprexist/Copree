"""世界技能的能力变更通知必须和平台工具走同一个出口（落账本）。

2026-10-21：拼【本群世界】那段里曾经自己 `build_change_notice` + `messages.append` + `commit`。
那条通知只存在于这一轮请求里，下一轮就没了——AI 会继续按旧说法办事，正是账本机制当初要修的
那个病（见 services/history/context_sync.py 的 docstring："以前只 append 进当轮 messages，
说完就没了"）。现在由 _collect_injection_events 统一算，经 append_events 落条目。
"""
import pytest
from sqlalchemy import text

pytestmark = pytest.mark.anyio

SRC = "world-999"


def _defs(*names: str) -> list:
    return [{"type": "function", "function": {"name": n, "description": ""}} for n in names]


async def _seed(db):
    from db_reset import clear

    await clear(db, "capability_versions", "agent_history_entries", "agent_user_configs",
                "agent_alarms", "pending_messages", "messages", "group_members", "groups",
                "agents", "users")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES "
        "(1, '群主', 'x', 'human'), (2, '测试AI账号', 'x', 'ai')"))
    await db.execute(text(
        "INSERT INTO agents (id, owner_id, name, user_id, discoverable, ai_type) "
        "VALUES (1, 1, '测试AI', 2, true, 'resonance')"))
    await db.execute(text(
        "INSERT INTO groups (id, name, owner_type, owner_id, avatar_mode, include_ai_in_avatar) "
        "VALUES (59, 'CoExisten', 'human', 1, 'default', true)"))
    await db.commit()


async def test_world_source_notice_lands_in_the_ledger(migrated_db):
    """世界技能的变更要按本状态的基线算，并落成账本条目（带 drop_on_unlock）"""
    from app.ai.llm import _collect_injection_events
    from app.database import async_session
    from app.models.agent import Agent
    from app.repositories.capability_repo import SQLAlchemyCapabilityRepository
    from app.services.capability_versioning import ensure_source_version, SCOPE_ALL
    from app.services.history import history_service
    from app.services.history.context_sync import append_events

    async with async_session() as db:
        await _seed(db)
        repo = SQLAlchemyCapabilityRepository(db)
        await ensure_source_version(repo, SRC, _defs("t1"), "世界技能", scope=SCOPE_ALL)
        agent = await db.get(Agent, 1)

        first = await _collect_injection_events(
            db, agent, "group:59", [], "agent-prompt-1", world_sources=[SRC])
        assert not [e for e in first if e["kind"] == "notice"], \
            "首见（这个状态还没有任何进度）不补发历史"

        await ensure_source_version(repo, SRC, _defs("t1", "t2"), "世界技能", scope=SCOPE_ALL)

        events = await _collect_injection_events(
            db, agent, "group:59", [], "agent-prompt-1", world_sources=[SRC])
        notices = [e for e in events if e["kind"] == "notice"]
        assert notices, "世界技能变了要发通知"
        assert any("v1→v2" in e["content"] for e in notices)
        assert notices[0]["flags"].get("drop_on_unlock"), "一次性事件：解锁即弃"

        await append_events(db, agent, "group:59", events)
        rows = await history_service.read(db, 1, "group:59")
        assert any(r["kind"] == "notice" and "v1→v2" in r["content"] for r in rows), \
            "通知必须落账本；只塞 messages 的话下一轮就没了"
