"""插件帧 —— 插件替 AI 建状态帧：命名空间、声明、建/改/收，以及 AI 仍能自己管。

契约：app/services/plugin/frames.py；文档：docs/plugin-dev/plugin-frames.md。

用例只用 migrated_db（conftest 提供）与普通函数：后端容器里的 run_without_pytest 不实现
tmp_path / monkeypatch 之类的 fixture，别在这里引入。
"""
import contextlib
import json
import shutil
import tempfile

import pytest
from sqlalchemy import select, text

pytestmark = pytest.mark.anyio

AI_ID, AI_USER_ID = 31, 47
PLUGIN = "demo"
DECLARED = {
    "demo_reading": {
        "title": "读笔记", "title_en": "Reading", "title_ja": "読む",
        "tools": ["notes_open", "pop_state"], "skills": [],
    }
}


async def _seed(db):
    from db_reset import clear

    await clear(db, "agents", "users")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES "
        "(1, '主人', 'x', 'human'), (47, '甲AI', 'x', 'ai')"))
    await db.execute(text(
        "INSERT INTO agents (id, owner_id, name, user_id, discoverable, state_stack) VALUES "
        "(31, 1, '甲AI', 47, true, '[]'::jsonb)"))
    await db.commit()


async def _agent(db):
    from app.models.agent import Agent

    return (await db.execute(select(Agent).where(Agent.id == AI_ID))).scalars().one()


@contextlib.contextmanager
def _as_plugin(plugin_id: str = PLUGIN, declared: dict | None = None):
    """装成插件加载上下文：当前插件 id + manifest 声明。用完一律还原。"""
    from app.services.plugin import api, frames

    block = DECLARED if declared is None else declared
    original = frames.declared_frames
    frames.declared_frames = lambda pid: block if pid == plugin_id else {}
    api.set_current_plugin(plugin_id)
    try:
        yield
    finally:
        frames.declared_frames = original
        api.set_current_plugin(None)


def test_manifest_declaration():
    """manifest 的 frames 段：合法类型收下，类型名不合法或不是对象的条目丢掉。"""
    from app.services.plugin import catalog, frames

    root = tempfile.mkdtemp(prefix="plugin-frames-")
    try:
        plugin_dir = f"{root}/demo"
        import os
        os.makedirs(plugin_dir)
        with open(f"{plugin_dir}/plugin.json", "w", encoding="utf-8") as fh:
            json.dump({
                "id": "demo",
                "frames": {
                    "demo_reading": {"title": "读笔记", "tools": ["notes_open"]},
                    "Bad Name": {"title": "不合法"},
                    "demo_bad": "not-an-object",
                },
            }, fh, ensure_ascii=False)

        original = catalog.USER_PLUGIN_DIR
        from pathlib import Path
        catalog.USER_PLUGIN_DIR = Path(root)
        catalog.invalidate_caches()
        try:
            declared = frames.declared_frames("demo")
        finally:
            catalog.USER_PLUGIN_DIR = original
            catalog.invalidate_caches()
    finally:
        shutil.rmtree(root, ignore_errors=True)

    assert declared["demo_reading"]["tools"] == ["notes_open"]
    # 缺英文名时退回 title，前端不必处理空值
    assert declared["demo_reading"]["title_en"] == "读笔记"
    assert "Bad Name" not in declared
    assert "demo_bad" not in declared


def test_decorators_read_the_contextvar_not_the_raw_variable():
    """@service 必须走 get_current_plugin()。

    直接读模块全局在 ContextVar 化之后会拿到对象本身（恒为真），服务声明就被注册到错的键下，
    按插件 id 查是 None——目录服务插件一个实例都建不起来（线上症状：QQ 通道完全收不到消息）。
    """
    from app.services.plugin import api
    from app.services.plugin.api import service, ServicePlugin

    api.set_current_plugin("demo-ctx")
    try:
        @service(name="演示", multi_instance=True)
        class Demo(ServicePlugin):
            id = "demo-ctx"

        defn = api.get_service_def("demo-ctx")
        assert defn is not None, "服务声明没按插件 id 注册"
        assert defn.plugin_id == "demo-ctx"
        assert defn.multi_instance is True
    finally:
        api.drop_service_def("demo-ctx")
        api.set_current_plugin(None)


def test_runtime_context_carries_the_plugin_id():
    """工具分发 / 服务生命周期会给插件补上身份：加载期之外也调得动帧接口，且并发不串号。"""
    from app.services.plugin import api

    assert api.get_current_plugin() is None
    with api.plugin_context("demo"):
        assert api.get_current_plugin() == "demo"
        with api.plugin_context("other"):
            assert api.get_current_plugin() == "other"
        assert api.get_current_plugin() == "demo", "内层退出后要还原外层"
    assert api.get_current_plugin() is None


async def test_enter_update_leave_revives_same_frame(migrated_db):
    """建帧 → 改文本 → 收掉 → 再进同一 context_ref：复活原帧，不压第二条。"""
    from app.database import async_session
    from app.services.agent.state_stack_service import get_active_frame_tools, get_frames
    from app.services.plugin import api

    async with async_session() as db:
        await _seed(db)
        agent = await _agent(db)
        with _as_plugin():
            frame = await api.enter_frame(
                db, agent, context_ref="demo:reading:1", type="demo_reading",
                label="读《甲》", doing="读到第 3 章",
            )
            await db.commit()
            assert frame["context_ref"] == "demo:reading:1"
            assert frame["tools"] == ["notes_open", "pop_state"]
            tools, _skills = await get_active_frame_tools(db, AI_ID)
            assert tools == ["notes_open", "pop_state"], "声明里的白名单要落到帧上，隔离走 frame_tool_gate"

            await api.update_frame(db, agent, frame["id"], doing="读到第 5 章")
            await db.commit()
            stored = [f for f in await get_frames(db, AI_ID) if f["id"] == frame["id"]][0]
            assert stored["doing"] == "读到第 5 章"

            await api.leave_frame(db, agent, frame["id"])
            await db.commit()
            ended = [f for f in await get_frames(db, AI_ID) if f["id"] == frame["id"]][0]
            assert ended["status"] == "ended"

            again = await api.enter_frame(
                db, agent, context_ref="demo:reading:1", type="demo_reading", doing="读到第 6 章")
            await db.commit()
            assert again["id"] == frame["id"], "同一段插件会话永远只有一帧"
            same = [f for f in await get_frames(db, AI_ID) if f.get("context_ref") == "demo:reading:1"]
            assert len(same) == 1 and same[0]["doing"] == "读到第 6 章"


async def test_frame_budget_reads_limits_and_blocks_when_used_up(migrated_db):
    """帧预算：轮次与小时/天上限来自插件配置；小时用满后 blocked_reason 不再为空。"""
    from app.database import async_session
    from app.services.agent import frame_budget
    from app.services.agent.state_stack_service import bump_frame_call_count
    from app.services.plugin import api

    async with async_session() as db:
        await _seed(db)
        agent = await _agent(db)
        # 整段都在插件上下文里：识别「这是插件帧」要读 manifest 声明，声明同样来自这里
        with _as_plugin():
            await api.enter_frame(
                db, agent, context_ref="demo:reading:9", type="demo_reading")
            await db.commit()

            original = frame_budget._config

            async def fake_config(plugin_id, agent_id):
                return {"frame_round_limit": 12, "frame_hour_limit": 2, "frame_day_limit": 100}

            frame_budget._config = fake_config
            try:
                # 开轮：普通对话的 6 次被帧上的 12 次取代，且这时还没用满
                turn = await frame_budget.open_turn(db, AI_ID, 6)
                assert turn.max_loops == 12
                assert turn.blocked == ""
                for _ in range(2):
                    await bump_frame_call_count(db, AI_ID)
                await db.commit()
                reason = await frame_budget.blocked_reason(db, AI_ID)
                assert "这一小时" in reason, reason
                # 中途复查（executor 每次调用前调）与开轮看的是同一份账
                assert "这一小时" in await turn.check()
            finally:
                frame_budget._config = original


def test_plain_frames_are_never_taken_for_plugin_frames():
    """群 / 私信 / 世界帧也带 `前缀:`，但不是插件帧。

    认错一次就够了：普通对话的轮次上限会变成 50、记账会从通道额度改成帧额度。
    """
    from app.services.agent.frame_budget import plugin_of

    assert plugin_of({"type": "group_chat", "context_ref": "group:66"}) == ""
    assert plugin_of({"type": "dm", "context_ref": "dm:1_2"}) == ""
    assert plugin_of({"type": "world", "context_ref": "world:3"}) == ""
    assert plugin_of({"type": "group_chat"}) == ""
    assert plugin_of(None) == ""
    # 声明过的插件帧才认
    with _as_plugin():
        assert plugin_of({"type": "demo_reading", "context_ref": "demo:reading:1"}) == "demo"
        assert plugin_of({"type": "demo_reading", "context_ref": "group:1"}) == ""


async def test_switch_false_parks_without_moving_the_ai(migrated_db):
    """switch=False 只建沉睡帧：进存储、不改变当前在哪。"""
    from app.database import async_session
    from app.services.agent.state_stack_service import (
        ensure_active_frame, get_current_frame, get_frames,
    )
    from app.services.plugin import api

    async with async_session() as db:
        await _seed(db)
        agent = await _agent(db)
        await ensure_active_frame(db, AI_ID, "group_chat", "group:59", "甲群", "某人")
        await db.commit()
        before = await get_current_frame(db, AI_ID)

        with _as_plugin():
            frame = await api.enter_frame(
                db, agent, context_ref="demo:reading:2", type="demo_reading", switch=False)
            await db.commit()

        stored = [f for f in await get_frames(db, AI_ID) if f["id"] == frame["id"]][0]
        assert stored["status"] == "ended", "沉睡帧不进状态链"
        after = await get_current_frame(db, AI_ID)
        assert after["id"] == before["id"], "预建不该把他从群会话里拽走"


async def test_entry_calls_the_plugin_hook_once(migrated_db):
    """进帧之后通知插件一次（on_frame_enter）：补消息、写账本都在这里发生。

    钩子是可选的：没实现就跳过；实现了就在**切换完成之后**拿到那一帧。
    """
    from app.database import async_session
    from app.services.agent.state_stack_service import (
        ensure_active_frame, get_current_frame,
    )
    from app.services.infrastructure.plugin_registry import PluginRegistry
    from app.services.plugin import api
    from app.tools.self_management.enter_state import EnterState

    seen: list[dict] = []

    class FakeChannel:
        owner, id, instance, name = "demo", "demo", "agent-31", "读笔记"
        key = "demo:agent-31"

        async def on_frame_enter(self, db, agent, frame):
            seen.append({"frame": frame.get("id"), "agent": agent.id})

    async with async_session() as db:
        await _seed(db)
        agent = await _agent(db)
        await ensure_active_frame(db, AI_ID, "group_chat", "group:59", "甲群", "某人")
        # 声明在整段里都得可见：平台要按「前缀 + 声明过的类型」才认这是插件帧
        with _as_plugin():
            parked = await api.enter_frame(
                db, agent, context_ref="demo:reading:8", type="demo_reading", switch=False)
            await db.commit()

            PluginRegistry.register(FakeChannel())
            try:
                result = await EnterState().execute(db, AI_ID, 59, {"frame_id": parked["id"]}, {})
                assert result.get("success"), result
                assert seen == [{"frame": parked["id"], "agent": AI_ID}], seen
                assert (await get_current_frame(db, AI_ID))["id"] == parked["id"]
            finally:
                PluginRegistry.unregister("demo:agent-31")


async def test_ai_enters_the_parked_frame_itself(migrated_db):
    """「他想进的时候进」：预建的沉睡帧由 AI 自己用 enter_state 进去，pop_state 出来。

    帧上的工具白名单随帧走（这里断言 demo 声明的那两个），原群会话被暂停而不是丢掉。
    """
    from app.database import async_session
    from app.services.agent.state_stack_service import (
        ensure_active_frame, get_current_frame,
    )
    from app.services.plugin import api
    from app.tools.self_management.enter_state import EnterState

    async with async_session() as db:
        await _seed(db)
        agent = await _agent(db)
        await ensure_active_frame(db, AI_ID, "group_chat", "group:59", "甲群", "某人")
        await db.commit()
        group_frame = await get_current_frame(db, AI_ID)

        with _as_plugin():
            parked = await api.enter_frame(
                db, agent, context_ref="demo:reading:7", type="demo_reading", switch=False)
            await db.commit()

        tool = EnterState()
        result = await tool.execute(db, AI_ID, 59, {"frame_id": parked["id"]}, {})
        assert result.get("success"), result
        entered = await get_current_frame(db, AI_ID)
        assert entered["id"] == parked["id"]
        assert entered["status"] == "active"
        assert entered["tools"] == ["notes_open", "pop_state"], "白名单随帧生效"
        # 原来那帧只是被压住（还能 pop_state 回来），没被结束掉
        from app.services.agent.state_stack_service import list_states

        prev = [f for f in await list_states(db, AI_ID) if f["id"] == group_frame["id"]][0]
        assert prev["status"] == "suspended"

        # 进去之后按帧上的工具走：出来用 pop_state
        from app.tools.self_management.pop_state import PopState

        popped = await PopState().execute(db, AI_ID, 59, {}, {})
        assert popped.get("success"), popped
        back = await get_current_frame(db, AI_ID)
        assert back["id"] == group_frame["id"]


async def test_namespace_and_declaration_are_enforced(migrated_db):
    """插件代建只能在自己命名空间、只能建声明过的类型。"""
    from app.database import async_session
    from app.services.plugin import api

    async with async_session() as db:
        await _seed(db)
        agent = await _agent(db)
        with _as_plugin():
            with pytest.raises(api.FrameError):
                await api.enter_frame(db, agent, context_ref="group:59", type="demo_reading")
            with pytest.raises(api.FrameError):
                await api.enter_frame(db, agent, context_ref="demo:x", type="not_declared")


async def test_ai_can_close_the_plugin_frame_itself(migrated_db):
    """帧是 AI 的：它的 close_state 工具（同一个服务函数）能直接收掉插件建的帧。"""
    from app.database import async_session
    from app.services.agent.state_stack_service import close_state, get_frames
    from app.services.plugin import api

    async with async_session() as db:
        await _seed(db)
        agent = await _agent(db)
        with _as_plugin():
            frame = await api.enter_frame(
                db, agent, context_ref="demo:reading:3", type="demo_reading", doing="读到一半")
            await db.commit()

        await close_state(db, AI_ID, frame["id"])   # tools/self_management/close_state.py 走的就是它
        await db.commit()
        stored = [f for f in await get_frames(db, AI_ID) if f["id"] == frame["id"]][0]
        assert stored["status"] == "ended"


async def test_cannot_touch_another_plugins_frame(migrated_db):
    """别的插件建的帧改不动、也收不掉（命名空间之外一律拒绝）。"""
    from app.database import async_session
    from app.services.agent.state_stack_service import get_frames
    from app.services.plugin import api

    async with async_session() as db:
        await _seed(db)
        agent = await _agent(db)
        with _as_plugin():
            frame = await api.enter_frame(
                db, agent, context_ref="demo:reading:4", type="demo_reading")
            await db.commit()

        api.set_current_plugin("other")
        try:
            with pytest.raises(api.FrameError):
                await api.update_frame(db, agent, frame["id"], doing="偷改")
            with pytest.raises(api.FrameError):
                await api.leave_frame(db, agent, frame["id"])
        finally:
            api.set_current_plugin(None)
        stored = [f for f in await get_frames(db, AI_ID) if f["id"] == frame["id"]][0]
        assert stored["doing"] == "" and stored["status"] == "active"
