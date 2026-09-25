"""
群/私信消息出口分发 —— 消息落库后，所有关心它的消费者都从这里接出去

为什么单独一层：消费者只会越来越多（绑定世界感知、QQ 通道、将来的微信通道……），
不能每加一个就往 send_gm_message 里塞一行 import，更不能让外部通道自己轮询数据库。

约定：
- sink 签名 async (db, group_id, message, source) / async (db, session_id, msg)
- **注册不以名字去重**：同一通道的多个实例（两个 QQ 机器人）各注册各的，分发时**并发**跑全部；
  一个坏/慢不拖累别的，也不拖累"发消息"本身（2026-09-25 线上事故：按名字覆盖注册，
  第二个 QQ 通道把第一个的出口顶掉，群 64 的 AI 回复被静默丢弃——所以改成句柄 + 并发）
- `register_sink` 返回**句柄**，stop 时用句柄注销（按名字注销 = 清掉该名字下所有出口）
- 调用发生在调用方 commit 之前（sink 内不要 commit 别人的 session）
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

Sink = Callable[[AsyncSession, int, Any, str], Awaitable[None]]
DmSink = Callable[[AsyncSession, str, dict], Awaitable[None]]

# 句柄 → (名字, sink)：句柄是注册时发的令牌，注销认它 —— 同名不再互相顶掉
_group_sinks: dict[str, tuple[str, Sink]] = {}
_dm_sinks: dict[str, tuple[str, DmSink]] = {}
_seq = 0


async def _world_sink(db: AsyncSession, group_id: int, message: Any, source: str) -> None:
    """内置 sink：群消息 → 绑定世界感知（喂给世界的 handle(event)，处理与否由世界决定）"""
    from app.services.world.world_event_hook import notify_group_message

    await notify_group_message(db, group_id, message, source)


def _next_handle(name: str) -> str:
    global _seq
    _seq += 1
    return f"{name}#{_seq}"


def register_sink(name: str, group: Sink | None = None, dm: DmSink | None = None) -> str:
    """注册一个通道出口，返回**句柄**（stop 时用它注销）。

    同名不去重：两个实例注册两次就是两个出口。一个通道通常两头都要（group + dm），只传一侧也行。
    """
    handle = _next_handle(name)
    if group is not None:
        _group_sinks[handle] = (name, group)
    if dm is not None:
        _dm_sinks[handle] = (name, dm)
    logger.info(f"消息出口已注册: {name}（handle={handle}，group={group is not None}，dm={dm is not None}）")
    return handle


def unregister_sink(handle_or_name: str) -> bool:
    """按**句柄**注销；传名字则注销该名字下所有出口（批量清理/兼容旧调用）。"""
    removed = False
    for store in (_group_sinks, _dm_sinks):
        if handle_or_name in store:
            store.pop(handle_or_name, None)
            removed = True
            continue
        for handle in [h for h, (n, _) in store.items() if n == handle_or_name]:
            store.pop(handle, None)
            removed = True
    return removed


def registered_sinks() -> dict[str, list[str]]:
    return {
        "group": sorted({name for name, _ in _group_sinks.values()}),
        "dm": sorted({name for name, _ in _dm_sinks.values()}),
    }


async def _fan_out(sinks: list[tuple[str, Any]], call) -> None:
    """并发跑所有出口：一个坏/慢不拖累别的（异常只记日志，绝不往上抛）。"""
    if not sinks:
        return
    results = await asyncio.gather(*(call(sink) for _, sink in sinks), return_exceptions=True)
    for (name, _), result in zip(sinks, results):
        if isinstance(result, Exception):
            logger.warning(f"消息出口「{name}」异常: {result}")


async def dispatch_group_message(
    db: AsyncSession, group_id: int, message: Any, source: str = "user"
) -> None:
    """群消息落库后分发（唯一调用点：send_gm_message）"""
    await _fan_out(list(_group_sinks.values()),
                   lambda sink: sink(db, group_id, message, source))


async def dispatch_dm_message(db: AsyncSession, session_id: str, msg: dict) -> None:
    """私信落库后分发（唯一调用点：send_dm_message）"""
    await _fan_out(list(_dm_sinks.values()), lambda sink: sink(db, session_id, msg))


register_sink("world", group=_world_sink)
