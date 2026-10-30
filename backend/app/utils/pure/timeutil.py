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

import logging
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# 时区库读不到时退到的固定偏移：展示时区就是 Asia/Shanghai，中国不实行夏令时，
# 固定 +08:00 与它在任何日期都一致，最多差一个时区名。
_FALLBACK_OFFSET = timedelta(hours=8)


def get_zoneinfo(name: str) -> tzinfo:
    """按名字取时区，取不到就退到 UTC+8。

    Windows 没有系统时区库，zoneinfo 只能从 tzdata 包读（见 requirements.txt 与
    Copree.spec）；Linux/macOS 走 /usr/share/zoneinfo。安装包若漏打包 tzdata，
    ZoneInfo(name) 会抛 ZoneInfoNotFoundError——这只是一次时间格式化，不该让整条
    回复崩掉，所以降级并留日志，把打包问题留给发布流程去修。
    """
    try:
        return ZoneInfo(name)
    except Exception:
        logger.warning("时区 %s 不可用（缺 tzdata？），本轮按 UTC+8 处理", name)
        return timezone(_FALLBACK_OFFSET)


def local_time_text(tz_name: str, now: datetime | None = None) -> str:
    """人读的当前时间：「2026-10-30 14:51 Asia/Shanghai（周五）」。

    模型的"现在几点"只有一个来源：提示词里这一行。各处自己拼（fmt 各写一遍、有的带星期有的不带）
    迟早漂：线上出现过半夜给自己排的「夜里的心跳」在第二天下午 14:51 触发，那一轮提示词里
    根本没有"现在"，模型只能拿账本里历史消息的时间戳当线索，于是照着任务里的"夜里"两个字回话。
    """
    tz = get_zoneinfo(tz_name)
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:            # 库里读出来的无时区值一律按 UTC 解释
        moment = moment.replace(tzinfo=timezone.utc)
    local = moment.astimezone(tz)
    name = getattr(tz, "key", None) or tz_name
    return f"{local.strftime('%Y-%m-%d %H:%M')} {name}（{_WEEKDAYS[local.weekday()]}）"


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
    local = (now or datetime.now(timezone.utc)).astimezone(get_zoneinfo(tz_name))
    return {
        "now": local.strftime("%H:%M"),
        "today": local.strftime("%Y-%m-%d"),
        "weekday": _WEEKDAYS[local.weekday()],
        "hour": local.hour,
    }
