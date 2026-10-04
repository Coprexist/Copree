"""
时间基准纯函数 — 无 IO、无 DB 依赖。

全库的时间戳列都是 timestamp without time zone，值一律按 UTC 存。两件事因此必须收口：

- 落库：aware 值会被 asyncpg 直接拒绝（invalid input for query argument），
  所以写库前一律走 utc_now() / to_db()，产出无时区 UTC；
- 运算：库里读出来的是无时区值，而 select now()、别的来源可能给带时区的，
  两者相减会 TypeError，所以比较前一律走 as_utc() 规范化。

数据库时区固定为 UTC（见迁移里的 ALTER DATABASE）：否则 func.now() 写进去的
就不是 UTC，那些无时区值会被按错误的时区解释。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo


def utc_now() -> datetime:
    """当前时刻，无时区 UTC——落库的唯一入口。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def as_utc(dt: datetime | None) -> datetime | None:
    """把任意来源的时刻规范成带 UTC 时区——运算的唯一入口（无时区的按 UTC 解释）。"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def to_db(dt: datetime | None = None) -> datetime:
    """落库用的值：无时区 UTC。不传则取当下。"""
    return utc_now() if dt is None else as_utc(dt).replace(tzinfo=None)


_WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def local_time_fields(tz_name: str, now: datetime | None = None) -> dict[str, Any]:
    """判定时刻的读数（now / today / weekday / hour）—— 条件与脚本读的"现在几点"。

    为什么是本地时间：AI 跟人说话用的是展示时区（settings.display_timezone），
    条件里写"晚上 8 点后"必须是它眼前的钟，给 UTC 只会算错。
    时区名由调用方传入：纯函数不读配置，测试也能钉住时刻。
    """
    local = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(tz_name))
    return {
        "now": local.strftime("%H:%M"),
        "today": local.strftime("%Y-%m-%d"),
        "weekday": _WEEKDAYS[local.weekday()],
        "hour": local.hour,
    }
