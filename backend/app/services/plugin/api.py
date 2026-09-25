"""
插件公共 API — 行为插件（声明 + 行为合一）的唯一入口。

行为插件 = 插件目录 + plugin.json（展示元数据）+ plugin.py（行为入口）：

    plugins/
      my-plugin/
        plugin.json     # id/name/description/category/icon/version
        plugin.py       # 用 @skill 装饰器声明并注册行为

plugin.py 示例：

    from app.services.plugin.api import skill

    @skill(
        type="keyword_autoreply",
        category="action",
        name="关键词自动回复",
        description="命中关键词时注入回复指令",
        config_schema={"keywords": {"type": "array", "items": {"type": "string"}}},
    )
    def handle(ctx):
        # ctx: {db, agent, skill, config, result, context, ...} 由分发器按需传入
        ...

一个装饰器同时完成两件事（单一来源，无双写）：
- 元数据 → SkillRegistry（与声明式 skill.json 完全同构）
- 行为 → skill_engine 的 _ACTION_HANDLERS / _INJECT_HANDLERS（owner 并入条目）

owner 由 skill_bridge 在加载时通过 set_current_plugin() 注入：装饰器执行时读取
当前正在加载的插件 id，注册进条目。加载结束后清空，防止模块顶层误注册。

语言中立契约：handler 收到的是普通 dict/对象上下文，返回效果写入 result——
不依赖任何 Python 特定机制，未来后端换语言时契约可平移。

服务插件（@service）走同一套目录契约，区别只在于它是"常驻服务"而不是"回复链路钩子"：

    plugins/qq-channel/
      plugin.json     # category: "service"
      plugin.py       # @service 装饰器声明并注册一个 ServicePlugin 实例

    from app.services.plugin.api import service, ServicePlugin

    @service(name="QQ 通道", description="...", config_schema={...})
    class QqChannelPlugin(ServicePlugin):
        async def start(self) -> bool: ...
        async def stop(self) -> bool: ...
        async def get_status(self) -> dict: ...

插件 id 取自正在加载的插件目录名（单一来源，作者不必写 id）；
内置服务插件（如 browser）不经装饰器，自行 PluginRegistry.register()。
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from dataclasses import dataclass

from app.services.infrastructure.plugin_registry import ServicePlugin
from app.services.skill.skill_engine import (
    register_action_handler,
    register_inject_handler,
)
from app.utils.pure.skill_registry import SkillRegistry

logger = logging.getLogger(__name__)

# 当前正在被 skill_bridge 加载的插件 id（装饰器执行时读取，作为 owner）
_current_plugin: str | None = None


def set_current_plugin(plugin_id: str | None) -> None:
    """skill_bridge 加载 plugin.py 前设置、加载后清空。"""
    global _current_plugin
    _current_plugin = plugin_id


def get_current_plugin() -> str | None:
    """当前加载上下文（测试与调试用）。"""
    return _current_plugin


def skill(
    type: str,
    category: str,
    name: str,
    description: str = "",
    config_schema: dict[str, Any] | None = None,
) -> Callable:
    """装饰器：声明一个技能类型并注册其行为处理器（一次完成）。

    :param type: 技能类型标识（唯一，与 AgentSkill.skill_type 对应）
    :param category: action（影响回复行为）或 inject（注入提示词）
    :param name: 显示名称
    :param description: 描述
    :param config_schema: 配置项 JSON Schema（前端表单用）
    """
    if category not in ("action", "inject"):
        raise ValueError(
            f"插件技能类别必须是 action 或 inject，收到 {category!r}（插件 {_current_plugin}）"
        )

    def wrapper(func: Callable) -> Callable:
        owner = _current_plugin  # skill_bridge 加载时注入；None = 非插件上下文
        SkillRegistry.register(
            type_name=type,
            name=name[:60],
            category=category,
            description=description,
            config_schema=config_schema or {},
        )
        register = register_action_handler if category == "action" else register_inject_handler
        register(type, owner=owner)(func)
        if owner:
            logger.info(f"行为插件已注册: {owner} -> {type} ({category})")
        return func

    return wrapper


@dataclass
class ServiceDef:
    """插件的服务声明：一个类 + 展示信息 + 配置 schema（可多实例）。

    声明与实例分离是"一个插件多份配置"的前提：QQ 通道接 3 个机器人 =
    1 个声明 + 3 个实例，而不是 3 个插件。
    """
    plugin_id: str
    cls: type
    name: str
    description: str
    config_schema: dict[str, Any]
    multi_instance: bool


_service_defs: dict[str, ServiceDef] = {}


def get_service_def(plugin_id: str) -> ServiceDef | None:
    return _service_defs.get(plugin_id)


def drop_service_def(plugin_id: str) -> None:
    """卸载时清掉声明（重扫会重新导入并重新声明）"""
    _service_defs.pop(plugin_id, None)


def service(
    name: str,
    description: str = "",
    config_schema: dict[str, Any] | None = None,
    multi_instance: bool = False,
) -> Callable:
    """装饰器：声明一个服务插件（**只声明，不实例化**）。

    实例化交给加载器：单实例插件用空串这一个实例；多实例插件按 DB 里配置的实例逐个建。
    与 @skill 同构——声明与行为合一，插件 id 取自正在加载的目录名。

    :param name: 显示名称
    :param description: 描述
    :param config_schema: 配置项 JSON Schema（前端表单用，secret: true 的项加密落库）
    :param multi_instance: 一个插件多份配置（每个实例一份，独立启停）
    """
    def wrapper(cls: type[ServicePlugin]) -> type[ServicePlugin]:
        plugin_id = _current_plugin or getattr(cls, "id", "") or cls.__name__.lower()
        _service_defs[plugin_id] = ServiceDef(
            plugin_id=plugin_id,
            cls=cls,
            name=name or getattr(cls, "name", plugin_id),
            description=description or getattr(cls, "description", ""),
            config_schema=config_schema or {},
            multi_instance=bool(multi_instance),
        )
        logger.info(
            f"服务插件已声明: {plugin_id}（{'多实例' if multi_instance else '单实例'}）"
        )
        return cls

    return wrapper


__all__ = [
    "skill",
    "service",
    "ServicePlugin",
    "ServiceDef",
    "get_service_def",
    "drop_service_def",
    "set_current_plugin",
    "get_current_plugin",
]
