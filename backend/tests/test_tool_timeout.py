"""工具执行超时的契约守卫（app/tools/base.py `dispatch`）。

背景：一个卡死的工具会占住整轮 AI 调用（私信/闹钟通道还是串行消费），
表现是「整个项目没反应」。超时把「永久阻塞」变成「一次可处理的失败」。

注意：超时能救的只有**异步挂起**。同步阻塞会占住事件循环，定时器根本排不上队，
那种情况归 `docs/dev/tool_execution.md` §3.5 的检测器管，不由本文件覆盖。
"""
from __future__ import annotations

import asyncio

import pytest

from _fakes import async_db
from app.tools.base import DEFAULT_TOOL_TIMEOUT_SECONDS, ToolErrorCode, ToolRegistry


class _SlowPlugin:
    """一个永远不返回的工具，用类属性声明短超时（不等 120s）。"""

    name = "test_forever_blocked_tool"
    owner = ""
    states: tuple[str, ...] = ()
    timeout_seconds = 0.05
    parameters = {"type": "object", "properties": {}}
    required: list[str] = []

    async def execute(self, db, agent_id, group_id, arguments, context):
        await asyncio.sleep(30)
        return {"success": True}  # 永远走不到


class _PerToolOverridePlugin(_SlowPlugin):
    name = "test_per_tool_timeout_override"
    timeout_seconds = 0.01


@pytest.fixture
def slow_plugin():
    plugin = _SlowPlugin()
    ToolRegistry._plugins[plugin.name] = plugin
    try:
        yield plugin
    finally:
        ToolRegistry._plugins.pop(plugin.name, None)


def _fake_db():
    # 状态闸读不到 agent 不拦；is_active=False 让 _recover_session 真的走回滚分支
    return async_db(active=False)


async def test_dispatch_times_out_and_reports_exec_failed(slow_plugin):
    result = await ToolRegistry.dispatch(
        _fake_db(), agent_id=1, group_id=None,
        tool_name=slow_plugin.name, arguments={}, context={},
    )
    assert result["error"] is True
    assert result["error_code"] == ToolErrorCode.TOOL_EXEC_FAILED
    assert "超时" in result["message"]


async def test_timeout_recovers_polluted_session(slow_plugin):
    db = _fake_db()
    await ToolRegistry.dispatch(
        db, agent_id=1, group_id=None,
        tool_name=slow_plugin.name, arguments={}, context={},
    )
    # 插件写到一半被取消，会话同样可能脏 → 必须复原，不能留给下一轮
    db.rollback.assert_awaited()


async def test_per_tool_timeout_overrides_default():
    """慢工具（世界运行、生图）能自己调长时间，不用被全局默认砍掉。"""
    assert _PerToolOverridePlugin().timeout_seconds < DEFAULT_TOOL_TIMEOUT_SECONDS
    plugin = _PerToolOverridePlugin()
    ToolRegistry._plugins[plugin.name] = plugin
    try:
        result = await ToolRegistry.dispatch(
            _fake_db(), agent_id=1, group_id=None,
            tool_name=plugin.name, arguments={}, context={},
        )
    finally:
        ToolRegistry._plugins.pop(plugin.name, None)
    assert result["error"] is True
    assert "超时" in result["message"]


def test_default_timeout_is_not_tiny():
    """默认值不许是几十秒——正常慢工具会被误杀成失败。"""
    assert DEFAULT_TOOL_TIMEOUT_SECONDS >= 60
