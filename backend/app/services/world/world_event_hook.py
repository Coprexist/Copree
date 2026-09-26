"""
世界事件钩子 — 群消息 → 世界程序感知

设计：
- 群消息入库后**异步喂给绑定世界的入口**（main.py handle(event)），
  处理不处理由世界程序自己决定（平台只喂，不干预）。
- 合并窗口（默认 2 秒，worlds.config.group_trigger_interval 可配；0 = 每条触发）：
  第一条消息**立即触发**（保证瞬时可达），随后窗口内到达的消息攒成一批、窗口结束时
  合并成一条 event.messages 再触发一次，避免群消息爆发把沙箱跑死。
- 防死循环：世界程序自己发的消息（send_gm_message source="world"）不触发。
- 触发不影响世界 status：沉睡世界也能感知；唤醒仍保持手动（AUTO_MANAGE=False）。

event 结构（世界代码在 handle(event) 里收到）：
{
  "type": "group_message",
  "group_id": 5,
  "source": "group",
  "messages": [
    {"message_id": 1, "sender_id": 2, "sender_name": "张三",
     "sender_type": "human", "content": "hi", "created_at": "2026-08-05T12:00:00"}
  ]
}
"""
import asyncio
import logging
from dataclasses import dataclass, field

from app.repositories.world_repo import SQLAlchemyWorldRepository
from sqlalchemy.ext.asyncio import AsyncSession
logger = logging.getLogger(__name__)

def _ensure_repo(db_or_repo):
    """兼容旧调用：传入 AsyncSession 时包装为 SQLAlchemyWorldRepository。"""
    if isinstance(db_or_repo, AsyncSession):
        return SQLAlchemyWorldRepository(db_or_repo)
    return db_or_repo


DEFAULT_INTERVAL = 2.0  # 默认合并窗口（秒）


@dataclass
class _Pending:
    world_id: int
    group_id: int
    msgs: list = field(default_factory=list)
    task: asyncio.Task | None = None
    window_open: bool = False


_pending: dict[int, _Pending] = {}


async def notify_group_message(db, group_id: int, message, source: str) -> None:
    """群消息钩子（send_gm_message 落库后调用）。

    - source="world"（世界程序自己发的消息）不触发，防死循环
    - 查绑定该群的世界 → 消息入队节流窗口（异步触发，不阻塞消息发送）
    """
    db = _ensure_repo(db)
    if source == "world":
        return
    from sqlalchemy import select
    from app.models.world import WorldBinding

    rows = (await db.execute(
        select(WorldBinding).where(
            WorldBinding.entity_type == "group",
            WorldBinding.entity_id == group_id,
        )
    )).scalars().all()
    if not rows:
        return
    for row in rows:
        await _enqueue(db, row.world_id, group_id, message)


async def _group_type_for_event(db, world_id: int, group_id: int) -> dict | None:
    """群消息事件注入：群绑定的类型轻量信息（{slug, name}）；未绑定返回 None。"""
    try:
        from app.services.world.group_type_service import get_group_type_for_group
        return await get_group_type_for_group(db, world_id, group_id)
    except Exception:
        return None


async def _enqueue(db, world_id: int, group_id: int, message) -> None:
    db = _ensure_repo(db)
    from app.models.world import World

    world = await db.get(World, world_id)
    if world is None:
        return
    interval = float((world.config or {}).get("group_trigger_interval", DEFAULT_INTERVAL))
    schedule(world_id, group_id, {
        "message_id": message.id,
        "sender_id": message.sender_id,
        "sender_type": message.sender_type,
        "content": message.content,
        "created_at": message.created_at.isoformat() if message.created_at else None,
    }, interval)


def schedule(world_id: int, group_id: int, msg: dict, interval: float) -> None:
    """合并窗口状态机（与 DB、事件投递解耦，用例可直接驱动）。

    语义：第一条立即触发，不让世界干等窗口；触发后开一个 interval 秒的窗口，
    窗口内到达的消息攒成一批，窗口结束时合并成一条事件再触发一次。
    投递一律丢进后台任务——钩子跑在消息落库路径上，不能拖慢发消息。
    """
    p = _pending.get(world_id)
    if p is None:
        p = _Pending(world_id=world_id, group_id=group_id)
        _pending[world_id] = p
    p.group_id = group_id
    p.msgs.append(msg)
    if interval <= 0:
        _fire(p)                                # 0 = 每条立即触发，不合并
        return
    if not p.window_open:
        p.window_open = True
        p.task = asyncio.create_task(_window(p, interval))
        _fire(p)


def _drain(p: _Pending) -> list:
    """同步取走缓冲：取走与投递之间不能再有消息混进来（否则"每条一批"就变成"看运气"）"""
    msgs, p.msgs = p.msgs, []
    return msgs


def _fire(p: _Pending) -> None:
    """取走缓冲并丢后台投递——钩子跑在消息落库路径上，不能等世界跑完"""
    msgs = _drain(p)
    if msgs:
        asyncio.create_task(_deliver_batch(p.world_id, p.group_id, msgs))


async def _deliver_batch(world_id: int, group_id: int, msgs: list) -> None:
    try:
        await _deliver(world_id, group_id, msgs)
    except Exception as e:
        logger.warning(f"🌐 世界 #{world_id} 群消息投递异常: {e}")


async def _window(p: _Pending, interval: float) -> None:
    """合并窗口计时：到点把窗口内攒的那批投递掉，然后关窗"""
    try:
        await asyncio.sleep(interval)
        msgs = _drain(p)
        if msgs:
            await _deliver_batch(p.world_id, p.group_id, msgs)
    except asyncio.CancelledError:
        raise
    finally:
        p.window_open = False
        p.task = None
    # 关窗瞬间到的消息不能留在缓冲里等下一次（下一次可能是几分钟后）——补一次
    if p.msgs:
        p.window_open = True
        p.task = asyncio.create_task(_window(p, interval))
        _fire(p)


async def _deliver(world_id: int, group_id: int, msgs: list) -> None:
    """投递一批消息给世界程序：查发件人名字 → 常驻进程或临时触发 handle(event)"""
    from app.database import async_session
    from sqlalchemy import select
    from app.models.user import User

    try:
        async with async_session() as db:
            from app.models.world import World
            world = await db.get(World, world_id)
            if world is None:
                return
            # 批量查发件人名字
            sender_ids = {m["sender_id"] for m in msgs}
            name_map: dict[int, str] = {}
            if sender_ids:
                u_res = await db.execute(select(User.id, User.username).where(User.id.in_(sender_ids)))
                name_map = dict(u_res.all())
            for m in msgs:
                m["sender_name"] = name_map.get(m["sender_id"], f"#{m['sender_id']}")
            event = {
                "type": "group_message",
                "group_id": group_id,
                "source": "group",
                "group_type": await _group_type_for_event(db, world_id, group_id),
                "messages": msgs,
            }
            # 2.5：常驻世界 → 投递常驻进程（进程内队列）；非常驻/未在跑 → 临时触发
            from app.services.world.world_resident import manager
            if manager.is_resident(world) and await manager.dispatch(world.id, event):
                return
            from app.services.world.world_sandbox import run_world_trigger
            # 确保沙箱 env 注入 WORLD_API_TOKEN / WORLD_API_BASE（懒生成）
            from app.routers.world_proxy import ensure_world_api_token
            await ensure_world_api_token(db, world)
            await db.commit()
            result = await run_world_trigger(world, event=event, background=True)
            if not result.get("success"):
                logger.info(f"🌐 世界 #{world_id} 群消息感知：程序未处理（{result.get('reason', '')[:80]}）")
    except Exception as e:
        logger.warning(f"🌐 世界 #{world_id} 群消息钩子执行异常: {e}")
