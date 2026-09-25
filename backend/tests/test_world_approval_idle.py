"""审批弹窗的计时契约（用户 2026-09-23 反馈）。

用户原话：计划弹窗等确认的时间太短，而且「正在编辑计划的输入框」时还在计时。
于是计时口径收成一个：**距上次活动**多久 —— 打字（touch 心跳）就续期，
但总时长另有天花板，免得一条审批把整个轮次无限挂住。
"""
from __future__ import annotations

import asyncio
import time
import uuid

from app.services.world import world_ai_mode as wam

WORLD_ID = 987654


def _entry() -> dict:
    now = time.monotonic()
    return {
        "id": uuid.uuid4().hex[:8], "world_id": WORLD_ID, "kind": "modify", "title": "t",
        "detail": "", "body": "", "body_format": "text", "body_lang": "",
        "created_at": time.time(), "started_at": now, "last_activity": now,
        "future": asyncio.get_running_loop().create_future(),
    }


def _register() -> dict:
    entry = _entry()
    wam._pending[entry["id"]] = entry
    return entry


def _unregister(entry: dict) -> None:
    wam._pending.pop(entry["id"], None)


async def test_typing_keeps_the_window_open():
    """空闲窗口 0.3s：一直打字就不超时——人还在写理由，不该被判超时。"""
    entry = _register()
    stop = False

    async def typer():
        while not stop:
            await asyncio.sleep(0.05)
            wam.touch_approval(entry["id"])

    task = asyncio.ensure_future(typer())
    try:
        wait = asyncio.ensure_future(wam._wait_decision(entry, 0.3, 10))
        await asyncio.sleep(0.6)
        assert not wait.done(), "打字期间不该超时"
        wam.resolve_approval(entry["id"], True, "同意，顺便把标题改短")
        assert await wait is True
        assert entry["note"] == "同意，顺便把标题改短"
    finally:
        stop = True
        task.cancel()
        _unregister(entry)


async def test_idle_window_expires_when_nobody_moves():
    """没人动 → 空闲窗口一到就超时，理由里说清是「没人操作」。"""
    entry = _register()
    try:
        await wam._wait_decision(entry, 0.2, 10)
        assert False, "没人操作必须超时"
    except wam.ApprovalTimeout as e:
        assert "没人操作" in str(e), str(e)
    finally:
        _unregister(entry)


async def test_hard_cap_beats_endless_typing():
    """一直打字也不行：总时长封顶后照样超时，不能把轮次无限挂住。"""
    entry = _register()
    stop = False

    async def typer():
        while not stop:
            await asyncio.sleep(0.05)
            wam.touch_approval(entry["id"])

    task = asyncio.ensure_future(typer())
    try:
        await wam._wait_decision(entry, 10, 0.3)
        assert False, "总时长封顶必须超时"
    except wam.ApprovalTimeout as e:
        assert "上限" in str(e), str(e)
    finally:
        stop = True
        task.cancel()
        _unregister(entry)


def test_touch_on_unknown_approval_is_a_noop():
    """心跳可能来自已经处理完的弹窗（另一个标签页先点了）→ 返回 0，不报错。"""
    assert wam.touch_approval("nope") == 0


def test_pending_list_carries_the_countdown():
    """刷新页面重画弹窗要靠 expires_in 接着走，而不是从头重数。"""
    entry = _register()
    try:
        rows = wam.pending_approvals(WORLD_ID)
        assert [r["approval_id"] for r in rows] == [entry["id"]]
        assert 0 < rows[0]["expires_in"] <= wam._APPROVAL_TIMEOUT
    finally:
        _unregister(entry)


def test_timeout_window_is_longer_than_before():
    """用户要求「稍微延长」：审阅/计划等确认的空闲窗口得比以前长，且总上限大于窗口。"""
    assert wam._APPROVAL_TIMEOUT >= 600
    assert wam._APPROVAL_MAX > wam._APPROVAL_TIMEOUT
