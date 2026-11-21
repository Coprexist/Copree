"""工具归属：内置工具归平台，插件工具归插件——卸载插件不许带走内置的。

2026-11-06 线上：重扫 bilibili 时回收了 94 个工具（send_gm / end_turn / pop_state /
set_alarm 都在里面），只因为它们注册时正好落在"加载 bilibili"的上下文里。根因是归属
判定读了加载上下文；现在改成看类的模块来源——`app.tools.*` 天然不属于任何插件。
"""
import pytest

from app.services.plugin import api
from app.tools.base import ToolPlugin, ToolRegistry

pytestmark = pytest.mark.anyio


def _make(name: str, module: str) -> type[ToolPlugin]:
    """造一个探针工具类：type() 会自动注册一次，先摘掉，改成想要的模块来源再手动注册。"""
    cls = type(name, (ToolPlugin,), {
        "name": name, "segment": "self_management", "description": "",
    })
    ToolRegistry._plugins.pop(name, None)
    ToolRegistry._invalidate_cache()
    cls.__module__ = module
    return cls


def _drop(*names: str) -> None:
    for name in names:
        ToolRegistry._plugins.pop(name, None)
    ToolRegistry._invalidate_cache()


def test_builtin_tool_keeps_platform_owner_even_inside_plugin_context():
    """内置工具在插件加载上下文里注册，也还是平台的。"""
    cls = _make("owner_probe_builtin", "app.tools.fake_segment.fake_tool")
    api.set_current_plugin("bilibili")
    try:
        ToolRegistry.register(cls)
    finally:
        api.set_current_plugin(None)

    assert ToolRegistry.get_plugin("owner_probe_builtin").owner == ""
    _drop("owner_probe_builtin")


def test_plugin_tool_is_owned_by_the_plugin_and_recycled_alone():
    """插件自己的工具归它，卸载只收它自己的；内置工具一个都不能少。"""
    ToolRegistry.register(_make("owner_probe_builtin2", "app.tools.fake_segment.fake_tool"))
    plugin_cls = _make("owner_probe_plugin", "_aisc_plugin_demo")
    api.set_current_plugin("demo")
    try:
        ToolRegistry.register(plugin_cls)
    finally:
        api.set_current_plugin(None)

    assert ToolRegistry.get_plugin("owner_probe_plugin").owner == "demo"
    assert ToolRegistry.unregister_owner("demo") == ["owner_probe_plugin"]
    assert ToolRegistry.get_plugin("owner_probe_builtin2") is not None, "卸载插件带走了内置工具"
    _drop("owner_probe_builtin2")
