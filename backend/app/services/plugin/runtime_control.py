"""服务实例启停 — 管理台与用户通道共用同一处

启停要同时做三件事：改运行态、把失败原因带回去、记下期望状态（重启后据此恢复）。
管理台要它，"用户给自己的 AI 开通道"也要它，各写一份迟早不一致。
"""
from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class UnknownInstance(KeyError):
    """注册表里没有这个实例（通常意味着配置没生效或插件被全局关闭）"""


class StartFailed(RuntimeError):
    """启动失败，消息里带着插件自己记的原因（如未配置凭据）"""


async def _remember(db: AsyncSession, plugin_key: str, running: bool) -> None:
    from app.services.infrastructure.plugin_registry import split_key
    from app.services.plugin.config import set_desired_running

    plugin_id, instance = split_key(plugin_key)
    await set_desired_running(plugin_id, instance, running, db)


async def start_instance(db: AsyncSession, plugin_key: str) -> dict:
    from app.services.infrastructure.plugin_registry import PluginRegistry

    plugin = PluginRegistry.get(plugin_key)
    if plugin is None:
        raise UnknownInstance(plugin_key)
    name = plugin.display_name()
    status = await plugin.get_status()
    if status.get("running", False):
        await _remember(db, plugin_key, True)
        return {"running": True, "message": name + " 已在运行"}
    if await plugin.start():
        await _remember(db, plugin_key, True)
        return {"running": True, "message": name + " 已启动"}
    # 把插件自己记的原因带出去（如"未配置 AppID"）：管理页和用户卡片都要显示"为什么起不来"
    reason = str(getattr(plugin, "last_error", "") or "").strip()
    raise StartFailed(name + " 启动失败：" + reason if reason else name + " 启动失败")


async def stop_instance(db: AsyncSession, plugin_key: str) -> dict:
    from app.services.infrastructure.plugin_registry import PluginRegistry

    plugin = PluginRegistry.get(plugin_key)
    if plugin is None:
        raise UnknownInstance(plugin_key)
    name = plugin.display_name()
    status = await plugin.get_status()
    if not status.get("running", False):
        await _remember(db, plugin_key, False)
        return {"running": False, "message": name + " 已停止"}
    await plugin.stop()
    await _remember(db, plugin_key, False)
    return {"running": False, "message": name + " 已停止"}
