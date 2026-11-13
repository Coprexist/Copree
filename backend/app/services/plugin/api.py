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

import contextlib
import logging
from contextvars import ContextVar
from typing import Any, Callable

from dataclasses import dataclass

from app.services.infrastructure.plugin_registry import ServicePlugin
from app.services.skill.skill_engine import (
    register_action_handler,
    register_inject_handler,
)
from app.utils.pure.plugin_env import ENV_PUBLIC_KEYS, ENV_TEXT_KEY
from app.utils.pure.skill_registry import SkillRegistry

logger = logging.getLogger(__name__)

# 当前插件 id：加载期由 skill_bridge 设置（装饰器据此认领 owner），运行期由平台在
# 工具分发 / 服务生命周期上再放一次（插件运行时要调用插件公共接口时必须知道"我是谁"）。
# 用 ContextVar 而不是模块全局：同一进程里多个工具/服务并发执行，全局变量一撞就串号。
_current_plugin: ContextVar[str | None] = ContextVar("aisc_current_plugin", default=None)


def set_current_plugin(plugin_id: str | None) -> None:
    """skill_bridge 加载 plugin.py 前设置、加载后清空。"""
    _current_plugin.set(plugin_id)


def get_current_plugin() -> str | None:
    """当前插件上下文（加载期 / 工具分发 / 服务生命周期三种时候有值）。"""
    return _current_plugin.get()


@contextlib.contextmanager
def plugin_context(plugin_id: str | None):
    """运行期把"我是哪个插件"放回上下文。

    加载期之外（工具执行、服务 start/stop/get_status）平台用它在调用点包一层，
    插件因此可以在运行期调用 `api.enter_frame` 这类需要身份接口。
    """
    if not plugin_id:
        yield
        return
    token = _current_plugin.set(plugin_id)
    try:
        yield
    finally:
        _current_plugin.reset(token)


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
            f"插件技能类别必须是 action 或 inject，收到 {category!r}（插件 {get_current_plugin()}）"
        )

    def wrapper(func: Callable) -> Callable:
        owner = get_current_plugin()  # skill_bridge 加载时注入；None = 非插件上下文
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
        plugin_id = get_current_plugin() or getattr(cls, "id", "") or cls.__name__.lower()
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


async def report_notice(
    db,
    agent,
    context_ref: str,
    text: str,
    *,
    ref: str = "",
    transient: bool = True,
) -> list[dict]:
    """给某个会话的 AI 留一条账本通知 —— 插件写通知的唯一入口（公共契约）。

    插件只说三件事：哪段会话（context_ref）、说了什么（text）、这条是不是「投一次就够」
    （transient）。条目长什么样由平台定：类型是 notice、transient 才带 drop_on_unlock；
    「已读」（seen）永远是平台在请求真的发出去之后打的，插件不碰。

    业务幂等锚点写 ref（账本按它判「这条说过没有」，例：channel_mode:full）——
    业务数据放 ref、平台语义位放 flags，两者不混。

    与其他 append 一致：本函数只 flush，事务边界留给调用方（写完记得 commit）。
    """
    from app.services.history.context_sync import append_events
    from app.utils.pure.history import make_entry

    return await append_events(db, agent, context_ref, [make_entry(
        "notice", text, ref=ref,
        flags={"drop_on_unlock": True} if transient else {},
    )])


async def notify_agent(
    agent_id: int,
    text: str,
    *,
    ref: str = "",
    context: dict | None = None,
    context_ref: str = "",
    transient: bool = True,
    db=None,
) -> bool:
    """给某个 AI 留一条账本通知 —— 插件的一步入口（`report_notice` 之上的省事版）。

    插件只用说三件事：**谁**（agent_id）、**在哪**（context 或 context_ref）、**说了什么**（text）。
    开会话、查 agent、拼会话键、提交都由这里做——这些以前每个调用点各抄一遍
    （bilibili 抄了两遍、qq-channel 和帧预算各一遍）。

    - `context` 收工具 `execute()` 拿到的那个 dict（带 `group_id` / `session_id`）：
      有 `session_id` 就是那段私信，否则是 `group:<id>`。要更明确就传 `context_ref`，它优先。
    - 返回 True = 真写进去了。把"推进游标 / 记已读"绑在这个返回值上，
      就能保证「标成已读的东西一定还能翻到」。
    - `db` 不传：自己开会话、自己提交；传了：只写不提交（与 `report_notice` 同口径，
      让调用方把这条通知和自己那笔写放同一个事务）。

    条目长什么样（kind / `drop_on_unlock` / `seen`）仍由平台定，见 `report_notice`。
    """
    from sqlalchemy import select

    from app.database import async_session
    from app.models.agent import Agent
    from app.services.history.context_sync import context_ref as _context_ref

    body = (text or "").strip()
    if not body or not agent_id:
        return False
    where = context_ref or _context_ref(
        session_id=(context or {}).get("session_id") or None,
        group_id=(context or {}).get("group_id"),
    )

    async def _write(session) -> bool:
        agent = (await session.execute(
            select(Agent).where(Agent.id == int(agent_id))
        )).scalars().first()
        if agent is None:
            return False
        await report_notice(session, agent, where, body, ref=ref, transient=transient)
        return True

    if db is not None:
        return await _write(db)
    async with async_session() as session:
        wrote = await _write(session)
        await session.commit()
    return wrote


# 插件帧：声明与创建自己的状态帧（契约与边界见 docs/plugin-dev/plugin-frames.md）。
# 放在这里统一导出，插件只认 api 这一个入口，不直接 import services 内部实现。
from app.services.plugin.frames import (  # noqa: E402
    FrameError,
    declared_frames,
    enter_frame,
    leave_frame,
    list_frames,
    update_frame,
)


__all__ = [
    "skill",
    "service",
    "report_notice",
    "notify_agent",
    "ServicePlugin",
    "ServiceDef",
    "get_service_def",
    "drop_service_def",
    "set_current_plugin",
    "get_current_plugin",
    "plugin_context",
    "ENV_TEXT_KEY",
    "ENV_PUBLIC_KEYS",
    "enter_frame",
    "update_frame",
    "leave_frame",
    "list_frames",
    "declared_frames",
    "FrameError",
]
