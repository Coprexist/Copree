"""帧预算的纯算术：按「小时 / 天」固定桶计数，桶一变就从 0 重新算。

为什么不用定时任务：每次记账时拿「当前桶」和帧里存的桶比一下，不同就归零——
停机、重启、跨天都不会漏，也不需要额外的调度器。「天」按平台展示时区切，
和通道额度的窗口同一口径（见 services/channel/token_quota.py）。
"""
from __future__ import annotations

from datetime import datetime, timezone

HOUR_SECONDS = 3600
DEFAULT_ROUND_LIMIT = 50


def _now(now: datetime | None = None) -> datetime:
    return now or datetime.now(timezone.utc)


def hour_bucket(now: datetime | None = None) -> int:
    """小时桶：epoch 秒整除 3600（与显示时区无关，整点换桶）。"""
    return int(_now(now).timestamp()) // HOUR_SECONDS


def day_bucket(now: datetime | None = None) -> str:
    """天桶：展示时区下的日期串（YYYY-MM-DD）。"""
    from zoneinfo import ZoneInfo

    from app.config import settings

    try:
        tz = ZoneInfo(settings.display_timezone or "UTC")
    except Exception:  # noqa: BLE001 —— 时区名配错不该让计数整个挂掉
        tz = timezone.utc
    return _now(now).astimezone(tz).strftime("%Y-%m-%d")


def bump(usage: dict | None, calls: int = 1, now: datetime | None = None) -> dict:
    """就地累加 usage：{hour: {key, n}, day: {key, n}}；桶变了先归零。"""
    usage = usage if isinstance(usage, dict) else {}
    for slot, key in (("hour", hour_bucket(now)), ("day", day_bucket(now))):
        cell = usage.get(slot)
        if not isinstance(cell, dict) or cell.get("key") != key:
            cell = {"key": key, "n": 0}
        cell["n"] = int(cell.get("n") or 0) + int(calls)
        usage[slot] = cell
    return usage


def count(usage: dict | None, slot: str, now: datetime | None = None) -> int:
    """某个桶里已经用了几次；桶不是当前的就算 0（跨桶自动归零）。"""
    cell = (usage or {}).get(slot) or {}
    key = hour_bucket(now) if slot == "hour" else day_bucket(now)
    if not isinstance(cell, dict) or cell.get("key") != key:
        return 0
    return int(cell.get("n") or 0)


def limit(value) -> int:
    """次数上限：空 / 0 / 非法 = 不限（返回 0）。"""
    try:
        num = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, num)


def round_limit(value) -> int:
    """一轮（一次唤醒）最多几次 LLM 调用：没配或配了 0 就用默认 50。"""
    return limit(value) or DEFAULT_ROUND_LIMIT


def ratio(used: int, cap: int) -> float:
    return (used / cap) if cap > 0 else 0.0


def describe(round_limit: int, hour_used: int = 0, hour_limit: int = 0,
             day_used: int = 0, day_limit: int = 0,
             token_percent: float = 0.0, session_minutes: int = 0) -> str:
    """帧预算的一句话（进状态摘要：AI 每轮都看得见自己的限制与已用量）。

    0 = 不限的档位不念——"不限"占了字数却不提供信息；一轮的上限始终念，它决定这一轮能走多远。
    session_minutes 是创建者给的建议单次时长：**只是建议**，平台不按它掐断（会计数的都在前面几档）。
    """
    parts = [f"本轮上限 {int(round_limit)} 次"]
    if int(hour_limit) > 0:
        parts.append(f"这一小时 {int(hour_used)}/{int(hour_limit)} 次")
    if int(day_limit) > 0:
        parts.append(f"今天 {int(day_used)}/{int(day_limit)} 次")
    if token_percent > 0:
        parts.append(f"token 已用 {token_percent:.0%}")
    if int(session_minutes) > 0:
        parts.append(f"建议单次 {int(session_minutes)} 分钟")
    return " · ".join(parts)
