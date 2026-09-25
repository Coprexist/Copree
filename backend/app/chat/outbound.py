"""
群消息出口分发 —— 消息落库后，所有关心它的消费者都从这里接出去

为什么单独一层：消费者只会越来越多（绑定世界感知、QQ 通道、将来的微信通道……），
不能每加一个就往 send_gm_message 里塞一行 import，更不能让外部通道自己轮询数据库。
世界感知成为这里的第一个 sink，行为与以前完全一致。

约定：
- sink 签名 async (db, group_id, message, source)
- 单个 sink 抛异常只记日志：外部通道坏了不能拖垮"发消息"本身
- 注册/注销成对（通道插件 start 注册、stop 注销），停用后不再收消息
- 调用发生在调用方 commit 之前（与原来的世界钩子同一位置），sink 内不要 commit 别人的 session
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

Sink = Callable[[AsyncSession, int, Any, str], Awaitable[None]]
DmSink = Callable[[AsyncSession, str, dict], Awaitable[None]]

_group_sinks: dict[str, Sink] = {}
_dm_sinks: dict[str, DmSink] = {}


async def _world_sink(db: AsyncSession, group_id: int, message: Any, source: str) -> None:
    """内置 sink：群消息 → 绑定世界感知（喂给世界的 handle(event)，处理与否由世界决定）"""
    from app.services.world.world_event_hook import notify_group_message

    await notify_group_message(db, group_id, message, source)


_group_sinks["world"] = _world_sink


def register_sink(name: str, group: Sink | None = None, dm: DmSink | None = None) -> None:
    """注册一个通道出口（同名覆盖：重扫/重载时幂等）。

    一个通道通常两头都要：group 收群消息、dm 收私信。只传一侧也可以。
    """
    if group is not None:
        _group_sinks[name] = group
    if dm is not None:
        _dm_sinks[name] = dm
    logger.info(f"消息出口已注册: {name}（group={group is not None}, dm={dm is not None}）")


def unregister_sink(name: str) -> bool:
    removed = _group_sinks.pop(name, None) is not None
    removed = _dm_sinks.pop(name, None) is not None or removed
    return removed


def registered_sinks() -> dict[str, list[str]]:
    return {
        "group": sorted(_group_sinks),
        "dm": sorted(_dm_sinks),
    }


async def dispatch_group_message(
    db: AsyncSession, group_id: int, message: Any, source: str = "user"
) -> None:
    """群消息落库后分发（唯一调用点：send_gm_message）"""
    for name, sink in list(_group_sinks.items()):
        try:
            await sink(db, group_id, message, source)
        except Exception as e:
            logger.warning(f"群消息出口「{name}」异常（group #{group_id}）: {e}")


async def dispatch_dm_message(db: AsyncSession, session_id: str, msg: dict) -> None:
    """私信落库后分发（唯一调用点：send_dm_message）"""
    for name, sink in list(_dm_sinks.items()):
        try:
            await sink(db, session_id, msg)
        except Exception as e:
            logger.warning(f"私信出口「{name}」异常（session {session_id}）: {e}")
