"""会话环境落点：帧上的基线 / 判定 / 通知，以及插件取值的容错。"""
from contextlib import contextmanager

import pytest

pytestmark = pytest.mark.anyio

REF = "group:7"


async def _seed(db):
    from sqlalchemy import text

    from db_reset import clear

    await clear(db, "agent_history_entries", "agents", "users")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES "
        "(1, '主人', 'x', 'human'), (2, 'AI账号', 'x', 'ai')"))
    await db.execute(text(
        "INSERT INTO agents (id, owner_id, name, user_id) VALUES (47, 1, '古河渚', 2)"))
    await db.commit()


async def _open_frame(db):
    from app.services.agent.state_stack_service import ensure_active_frame
    await ensure_active_frame(db, 47, "group_chat", REF, title="群7", actor_name="某人")
    await db.commit()


async def _agent(db):
    from app.models.agent import Agent
    return await db.get(Agent, 47)


@contextmanager
def _stub_current(fn):
    """替换取值口，把判定与落点单独隔出来测（真实取值依赖活着的插件实例）。"""
    from app.services.plugin import environment as env_mod
    old = env_mod.current_environment
    env_mod.current_environment = fn
    try:
        yield
    finally:
        env_mod.current_environment = old


async def test_first_read_establishes_baseline_without_notice(migrated_db):
    from app.database import async_session
    from app.services.agent.state_stack_service import read_frame_env
    from app.services.history import history_service
    from app.services.plugin import environment as env_mod

    seen = {"channel": "qq", "origin_name": "泰拉都市", "member_num": 27}

    async def _fake(db_, group_id):
        return dict(seen)

    async with async_session() as db:
        await _seed(db)
        await _open_frame(db)
        agent = await _agent(db)
        with _stub_current(_fake):
            assert await env_mod.sync_environment(db, agent, 7) is None, "建立基线不发通知"
            await db.commit()
        have, locked, notified = await read_frame_env(db, 47, REF)
        assert have, "首次取值要把基线写进会话帧"
        assert locked == notified == seen
        assert await history_service.read(db, 47, REF) == [], "建立基线不该发通知"


async def test_change_lands_a_notice_and_keeps_the_locked_copy(migrated_db):
    from app.database import async_session
    from app.services.agent.state_stack_service import read_frame_env
    from app.services.history import history_service
    from app.services.plugin import environment as env_mod

    box = {"value": {"channel": "qq", "member_num": 27}}

    async def _fake(db_, group_id):
        return box["value"]

    async with async_session() as db:
        await _seed(db)
        await _open_frame(db)
        agent = await _agent(db)
        with _stub_current(_fake):
            await env_mod.sync_environment(db, agent, 7)
            await db.commit()
            box["value"] = {"channel": "qq", "member_num": 28}
            entry = await env_mod.sync_environment(db, agent, 7)
            await db.commit()
            assert entry and entry["kind"] == "notice", entry
            assert entry["content"].startswith("【环境变化】"), entry
            assert "28" in entry["content"]
            assert await env_mod.sync_environment(db, agent, 7) is None, "同一份值不再重复投递"
            await db.commit()
        have, locked, notified = await read_frame_env(db, 47, REF)
        assert notified == {"channel": "qq", "member_num": 28}, "已告知值要推进（去重依据）"
        assert locked == {"channel": "qq", "member_num": 27}, "锁定那份要等解锁点才换"


async def test_environment_disappearing_is_a_change(migrated_db):
    from app.database import async_session
    from app.services.plugin import environment as env_mod

    box = {"value": {"channel": "qq"}}

    async def _fake(db_, group_id):
        return box["value"]

    async with async_session() as db:
        await _seed(db)
        await _open_frame(db)
        agent = await _agent(db)
        with _stub_current(_fake):
            await env_mod.sync_environment(db, agent, 7)
            await db.commit()
            box["value"] = None
            assert await env_mod.sync_environment(db, agent, 7), "环境消失也算变更"
            await db.commit()


async def test_plugin_failure_is_dropped_not_treated_as_no_environment():
    from app.services.infrastructure.plugin_registry import PluginRegistry
    from app.services.plugin import channel as channel_mod
    from app.services.plugin import environment as env_mod

    class _Boom:
        async def environment(self, *, origin):
            raise RuntimeError("通道断线")

    class _Illegal:
        async def environment(self, *, origin):
            return ["not", "a", "dict"]

    async def _served(db, group_id):
        return [("qq-channel", "agent-47", {"kind": "qq"})]

    old_get, old_served = PluginRegistry.get, channel_mod.served_instances
    channel_mod.served_instances = _served
    try:
        PluginRegistry.get = staticmethod(lambda key: _Boom())
        assert await env_mod.current_environment(None, 7) is None, "取值异常要丢弃，不能当成无环境"
        PluginRegistry.get = staticmethod(lambda key: _Illegal())
        assert await env_mod.current_environment(None, 7) is None, "违约同样丢弃"
    finally:
        PluginRegistry.get, channel_mod.served_instances = old_get, old_served
