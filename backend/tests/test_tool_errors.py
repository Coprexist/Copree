"""工具错误协议（app/tools/errors.py）的契约守卫。

背景：插件作者此前只有 raise（被吞成「执行失败：xxx」，语义全丢）和
return {"error": True}（没有 code，模型不知道要不要重试）。协议把两者分开。
"""
from __future__ import annotations

import pytest

from _fakes import async_db
from app.tools.base import ToolErrorCode, ToolRegistry
from app.tools.errors import INVALID_ARGS, ToolError

pytestmark = pytest.mark.anyio


def _fake_db():
    # ToolError 不碰事务：is_active=True，不该触发回滚
    return async_db(active=True)


class _RejectingPlugin:
    name = "test_tool_error_plugin"
    owner = "demo-plugin"
    states: tuple[str, ...] = ()
    parameters = {"type": "object", "properties": {}}
    required: list[str] = []

    async def execute(self, db, agent_id, group_id, arguments, context):
        raise ToolError(INVALID_ARGS, "缺少 group_id")


class _BoomPlugin:
    name = "test_tool_boom_plugin"
    owner = "demo-plugin"
    states: tuple[str, ...] = ()
    parameters = {"type": "object", "properties": {}}
    required: list[str] = []

    async def execute(self, db, agent_id, group_id, arguments, context):
        raise RuntimeError("插件内部炸了")


def test_to_result_shape():
    err = ToolError(INVALID_ARGS, "缺少参数")
    result = err.to_result()
    assert result == {"error": True, "error_code": INVALID_ARGS, "message": "缺少参数"}


def test_retryable_and_outward_flags_are_forwarded():
    result = ToolError(INVALID_ARGS, "x", retryable=True, outward=True).to_result()
    assert result["retryable"] is True
    assert result["outward"] is True
    # 不给就不出现：别让模型以为「没写 retryable」= False 之外还有别的含义
    plain = ToolError(INVALID_ARGS, "x").to_result()
    assert "retryable" not in plain and "outward" not in plain


async def test_dispatch_returns_author_declared_code():
    plugin = _RejectingPlugin()
    ToolRegistry._plugins[plugin.name] = plugin
    try:
        result = await ToolRegistry.dispatch(
            _fake_db(), agent_id=1, group_id=None,
            tool_name=plugin.name, arguments={}, context={},
        )
    finally:
        ToolRegistry._plugins.pop(plugin.name, None)
    assert result["error"] is True
    assert result["error_code"] == INVALID_ARGS          # 语义保住了，不被改写成 EXEC_FAILED
    assert result["message"] == "缺少 group_id"


async def test_business_error_does_not_roll_back():
    """作者声明的业务错误没有半个写操作，不该顺手回滚别人的事务。"""
    db = _fake_db()
    plugin = _RejectingPlugin()
    ToolRegistry._plugins[plugin.name] = plugin
    try:
        await ToolRegistry.dispatch(
            db, agent_id=1, group_id=None,
            tool_name=plugin.name, arguments={}, context={},
        )
    finally:
        ToolRegistry._plugins.pop(plugin.name, None)
    db.rollback.assert_not_awaited()


async def test_uncaptured_exception_still_becomes_exec_failed():
    """作者没声明的异常仍归 EXEC_FAILED：协议只收「说得清的错误」。"""
    plugin = _BoomPlugin()
    ToolRegistry._plugins[plugin.name] = plugin
    try:
        result = await ToolRegistry.dispatch(
            _fake_db(), agent_id=1, group_id=None,
            tool_name=plugin.name, arguments={}, context={},
        )
    finally:
        ToolRegistry._plugins.pop(plugin.name, None)
    assert result["error_code"] == ToolErrorCode.TOOL_EXEC_FAILED
