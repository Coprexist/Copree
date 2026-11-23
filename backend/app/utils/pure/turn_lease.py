"""世界回合的租约：判断「这一轮还在跑」有几成可信

active_turn 是写进 worlds.config 的标记，status 接口据此报 processing=true，前端显示
「思考中」并订阅 SSE 直播。但标记只在轮次收尾的 finally 里清——进程被 kill -9、worker
任务异常退出、收尾时写库失败，都会留下一个没人负责的标记，于是前端「永远处理中」。

租约的解法：标记里除了 started_at 再记一个心跳 heartbeat_at，轮次执行期间定期刷新。
判断改成问「心跳还在租期里吗」，而不是「这个键在不在」。升级前写下的老标记没有心跳，
退回看 started_at——它同样能说明这一轮是不是早就死了。

这里只放纯时间判定（不碰库、不碰 asyncio），发送心跳的节奏由 world_turn 负责。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

# 轮次执行期间多久刷一次心跳
HEARTBEAT_SECONDS = 30
# 心跳消失多久算租约过期（三个心跳周期：容忍一次网络/GC 抖动，又不至于让人干等）
LEASE_TTL_SECONDS = 90


def parse_stamp(value: object) -> datetime | None:
    """解析落库的时间戳：naive ISO 字符串（与 write 侧同一口径），认不出就是 None。"""
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo is not None else value
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.replace(tzinfo=None) if parsed.tzinfo is not None else parsed


def lease_deadline(active_turn: dict | None) -> datetime | None:
    """租约的最后有效时刻：有心跳看心跳，没心跳退回开始时刻。"""
    if not active_turn:
        return None
    return parse_stamp(active_turn.get("heartbeat_at")) or parse_stamp(active_turn.get("started_at"))


def lease_alive(active_turn: dict | None, now: datetime | None = None) -> bool:
    """这条 active_turn 现在还算「进行中」吗。

    没有时间字段（结构损坏 / 手工写坏）按「活着」处理：宁可多报一次 processing，
    也不要在真的正在跑的时候把前端从「思考中」踢下来。
    """
    if not active_turn:
        return False
    deadline = lease_deadline(active_turn)
    if deadline is None:
        return True
    # 落库的是 naive UTC 串（world_turn._stamp 的口径），比较也必须用 naive UTC：
    # 容器时区通常是 Asia/Shanghai，用 datetime.now() 会凭空多出 8 小时，
    # 让每一轮刚开跑就被判成过期。
    moment = (now or datetime.now(timezone.utc).replace(tzinfo=None)).replace(tzinfo=None)
    return moment - deadline < timedelta(seconds=LEASE_TTL_SECONDS)
