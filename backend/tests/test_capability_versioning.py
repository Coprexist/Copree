"""能力版本化：锁定态的请求 tools 不许被平台发布改字节

设计见 docs/dev/capability_lazy_loading.md（不变式）+ docs/dev/frame_lifecycle.md（重建点表）。
"""


def _d(name: str) -> dict:
    return {"type": "function", "function": {"name": name, "description": ""}}


def test_keep_request_tools_pins_deleted_tools_and_keeps_state_gate():
    """已删的工具保留旧定义（解锁才消失）；状态闸仍然生效；平台还在但被闸掉的照旧剔除"""
    from app.services.capability_versioning import keep_request_tools
    from app.tools.base import ToolRegistry

    registered = {t["function"]["name"] for t in ToolRegistry.get_all_definitions()}
    assert "web_search" in registered, "测试前提：web_search 是平台现役工具"

    effective = [_d("web_search"), _d("file_read"), _d("platform_tool_已删除")]
    kept = {t["function"]["name"] for t in keep_request_tools(effective, {"web_search"})}

    assert "web_search" in kept, "允许集里的照发"
    assert "file_read" not in kept, "平台还在、但被状态闸挡住 → 不发"
    assert "platform_tool_已删除" in kept, "平台已删 → 保留旧定义到解锁为止（抠掉会断缓存）"
