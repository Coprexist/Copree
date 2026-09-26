"""群消息合并窗口：第一条立即触发（瞬时可达），随后窗口内的消息合并成一批。

语义见 backend/app/services/world/world_event_hook.py 模块头与 docs/dev/decision_layer.md。
"""
import asyncio

import pytest

pytestmark = pytest.mark.anyio


async def _drive(interval, script):
    """跑一遍窗口状态机（替换投递，只记录批次）"""
    from app.services.world import world_event_hook as hook

    calls: list[list[int]] = []
    orig = hook._deliver

    async def fake_deliver(world_id, group_id, msgs):
        calls.append([m["message_id"] for m in msgs])

    hook._deliver = fake_deliver
    hook._pending.clear()
    try:
        await script(hook, calls, interval)
        return calls
    finally:
        hook._deliver = orig
        for p in hook._pending.values():
            if p.task:
                p.task.cancel()
        hook._pending.clear()


async def test_first_message_fires_immediately_then_window_batches():
    """首条不等窗口；触发后的窗口内消息合并一次；窗口关闭后新消息又是立即的"""

    async def script(hook, calls, interval):
        hook.schedule(901, 1, {"message_id": 1, "sender_id": 1}, interval)
        await asyncio.sleep(0.05)
        assert calls == [[1]], calls           # 没等窗口，立刻就发

        hook.schedule(901, 1, {"message_id": 2, "sender_id": 1}, interval)
        hook.schedule(901, 1, {"message_id": 3, "sender_id": 1}, interval)
        assert calls == [[1]], calls           # 窗口内不重复触发

        await asyncio.sleep(0.35)
        assert calls == [[1], [2, 3]], calls   # 窗口到点合并成一批

        hook.schedule(901, 1, {"message_id": 4, "sender_id": 1}, interval)
        await asyncio.sleep(0.05)
        assert calls == [[1], [2, 3], [4]], calls

    assert await _drive(0.3, script) == [[1], [2, 3], [4]]


async def test_zero_interval_never_merges():
    """配 0 = 每条都立即触发，一行都不合并"""

    async def script(hook, calls, interval):
        hook.schedule(902, 1, {"message_id": 1, "sender_id": 1}, interval)
        hook.schedule(902, 1, {"message_id": 2, "sender_id": 1}, interval)
        await asyncio.sleep(0.05)
        assert calls == [[1], [2]] or calls == [[2], [1]], calls

    assert len(await _drive(0, script)) == 2
