"""
工具注册表（薄封装层）

所有工具已迁移到独立插件文件（backend/app/tools/）。
此文件保持向后兼容：外部 import 路径不变，内部委托给 ToolRegistry。
"""
import logging

logger = logging.getLogger(__name__)

# 导入 tools 包触发所有插件注册
import app.tools  # noqa: E402, F401 — 触发 ToolRegistry.register()
from app.tools.base import ToolRegistry, ToolErrorCode, _strip_delay_reply  # noqa: E402

# ============================================================
# 向后兼容的模块级全局变量（自动从 ToolRegistry 推导）
# ============================================================

@property
def _lazy_TOOL_DEFINITIONS():
    """惰性获取工具定义列表"""
    return ToolRegistry.get_all_definitions()


@property
def _lazy_TOOL_HANDLERS():
    """惰性获取工具 handler 映射"""
    return {name: plugin.execute for name, plugin in ToolRegistry.get_all_plugins().items()}


@property
def _lazy_STATE_TOOL_WHITELIST():
    """惰性获取状态白名单"""
    return ToolRegistry.get_whitelist()


@property
def _lazy_SKILL_SEGMENTS():
    """惰性获取技能段信息"""
    return ToolRegistry.get_segments()


# 为保持旧代码 `if tool_name in TOOL_HANDLERS` 等模式可用，
# 这里用 module __getattr__ 实现惰性代理（Python 3.7+）。
# 好处：按需计算，不影响 import 性能。
_lazy_attrs = {
    "TOOL_DEFINITIONS": lambda: ToolRegistry.get_all_definitions(),
    "TOOL_HANDLERS": lambda: {name: plugin.execute for name, plugin in ToolRegistry.get_all_plugins().items()},
    "STATE_TOOL_WHITELIST": lambda: ToolRegistry.get_whitelist(),
    "SKILL_SEGMENTS": lambda: ToolRegistry.get_segments(),
}


def __getattr__(name: str):
    """惰性模块属性代理 — 保持旧代码访问 module.TOOL_DEFINITIONS 等可用"""
    if name in _lazy_attrs:
        return _lazy_attrs[name]()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# ============================================================
# 向后兼容的函数接口（委托给 ToolRegistry）
# ============================================================

def get_allowed_tools(state: str, thinking_enabled: bool | None = None,
                      delay_reply_allowed: bool = True, tool_scope: str = "all") -> list[dict]:
    """根据 AI 状态与工具集范围返回允许使用的工具定义列表"""
    return ToolRegistry.get_allowed_tools(state, thinking_enabled, delay_reply_allowed, tool_scope)


async def dispatch_tool_call(
    db,
    agent_id: int,
    group_id: int,
    tool_name: str,
    arguments: dict,
    context: dict,
) -> dict:
    """统一分发工具调用"""
    return await ToolRegistry.dispatch(db, agent_id, group_id, tool_name, arguments, context)


def validate_tool_call(tool_name: str, arguments: dict) -> tuple[bool, str | None]:
    """校验工具调用格式"""
    return ToolRegistry.validate(tool_name, arguments)
