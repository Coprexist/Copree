"""世界回合的运行态：租约、重放、断点续

守三条线上症状：
- 进程被 kill -9 / worker 写库失败，留下 active_turn 标记 → status 永远 processing，
  前端"思考中"转到天荒地老（租约：心跳停了就当它死了）；
- 插入消息被 drain 走、落库前抛异常 → 既不在队列也不在库，用户消息凭空消失
  （在途清单：没确认落库就放回队列重放）；
- 含空条目的批次用「msg_ids 长度 == items 长度」判完成 → 永远判不完，收尾时整批重落，
  用户看到两条一样的消息（游标：按下标推进，空条目也走一格）。
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

pytestmark = pytest.mark.anyio


def _stamp(offset_seconds: int = 0) -> str:
    moment = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=offset_seconds)
    return moment.isoformat()


def test_lease_requires_fresh_heartbeat():
    from app.utils.pure.turn_lease import LEASE_TTL_SECONDS, lease_alive

    alive = {"turn_id": "t1", "started_at": _stamp(-3600), "heartbeat_at": _stamp()}
    assert lease_alive(alive) is True, "心跳是新鲜的：这一轮确实在跑"

    dead = {"turn_id": "t1", "started_at": _stamp(-3600), "heartbeat_at": _stamp(-LEASE_TTL_SECONDS - 5)}
    assert lease_alive(dead) is False, "心跳早停了：标记还在但没人负责"

    assert lease_alive(None) is False
    assert lease_alive({}) is False, "空标记不算进行中"


def test_lease_falls_back_to_started_at():
    """升级前写下的老标记没有 heartbeat_at，退回看开始时刻。"""
    from app.utils.pure.turn_lease import LEASE_TTL_SECONDS, lease_alive

    assert lease_alive({"turn_id": "t1", "started_at": _stamp(-5)}) is True
    assert lease_alive({"turn_id": "t1", "started_at": _stamp(-LEASE_TTL_SECONDS - 5)}) is False


def test_lease_uses_utc_not_local_clock():
    """落库是 naive UTC；拿本地时间比较会凭空差出一个时区（容器通常是 +08）。"""
    from app.utils.pure.turn_lease import lease_alive

    # 刚写下的心跳，用 UTC 判必须活着
    assert lease_alive({"turn_id": "t1", "heartbeat_at": _stamp()}) is True
    # 结构坏了（没有时间字段）按活着处理：宁可多报一次，也不要踢掉正在跑的轮次
    assert lease_alive({"turn_id": "t1"}) is True


def _worker(world_id: int):
    from app.services.world.world_turn import WorldTurnWorker

    worker = WorldTurnWorker(world_id)
    worker.task.cancel()  # 测试只要队列与租约行为，不要后台循环
    return worker


async def test_drained_inserts_are_requeued_until_confirmed():
    from app.services.world.world_chat_items import ChatItem

    worker = _worker(987654)
    try:
        entry = {"user_id": 1, "items": [ChatItem(text="喂")], "msg_ids": [], "tb": None}
        worker._inserts.append(entry)

        drained = await worker.drain_inserts()
        assert drained == [entry] and worker._inserts == []
        assert worker._inflight_inserts == [entry], "drain 之后必须留一份在途，否则异常时凭空消失"

        # 没确认落库 → 收尾放回队列，下一轮重放
        await worker._requeue_inflight_inserts()
        assert worker._inserts == [entry] and worker._inflight_inserts == []

        # 确认之后同一批只销一次账
        again = await worker.drain_inserts()
        worker.confirm_inserts(again)
        assert worker._inserts == [] and worker._inflight_inserts == []
    finally:
        await asyncio.gather(worker.task, return_exceptions=True)


async def test_heartbeat_keeps_lease_alive(migrated_db):
    from db_reset import clear
    from app.database import async_session
    from app.models.user import User
    from app.models.world import World
    from app.utils.pure.turn_lease import lease_alive
    from app.services.world import world_turn

    async with async_session() as db:
        await clear(db, "worlds", "users")
        owner = User(username="世界主人", password_hash="x", type="human")
        db.add(owner)
        await db.flush()
        world = World(name="心跳世界", owner_id=owner.id, config={"active_turn": {
            "turn_id": "t1", "started_at": _stamp(-600), "heartbeat_at": _stamp(-600),
        }})
        db.add(world)
        await db.commit()
        world_id = world.id

    worker = _worker(world_id)
    original_interval = world_turn.HEARTBEAT_SECONDS
    world_turn.HEARTBEAT_SECONDS = 0.01
    try:
        task = asyncio.create_task(worker._heartbeat("t1"))
        await asyncio.sleep(0.05)
        await worker._stop_heartbeat(task)
    finally:
        world_turn.HEARTBEAT_SECONDS = original_interval
        await asyncio.gather(worker.task, return_exceptions=True)

    async with async_session() as db:
        refreshed = await db.get(World, world_id)
        active = refreshed.config["active_turn"]
        assert lease_alive(active) is True, "心跳应当把租约续上"


async def test_persist_insert_entry_resumes_from_cursor(migrated_db):
    """含空条目的批次：落库进度按下标推进，重放不会重复落。"""
    from db_reset import clear
    from app.database import async_session
    from app.models.user import User
    from app.models.world import World, WorldChatMessage
    from app.services.world.world_chat_items import ChatItem
    from app.services.world.world_turn import persist_insert_entry

    async with async_session() as db:
        await clear(db, "worlds", "users")
        owner = User(username="世界主人", password_hash="x", type="human")
        db.add(owner)
        await db.flush()
        world = World(name="落库世界", owner_id=owner.id)
        db.add(world)
        await db.commit()
        world_id = world.id

        items = [ChatItem(text="第一条"), ChatItem(text=""), ChatItem(text="第三条")]
        entry = {"user_id": owner.id, "items": items, "msg_ids": [], "tb": None}

        next_index, persisted = await persist_insert_entry(db, world_id, entry, None)
        await db.commit()
        entry["cursor"] = next_index
        assert (next_index, len(persisted)) == (3, 2), "空条目只占游标、不落库"

        again_index, again = await persist_insert_entry(db, world_id, entry, None)
        await db.commit()
        assert again_index == 3 and again == [], "游标已到位：重放是空操作"

        rows = (await db.execute(
            select(WorldChatMessage).order_by(WorldChatMessage.id)
        )).scalars().all()
        assert [r.content for r in rows] == ["第一条", "第三条"]
