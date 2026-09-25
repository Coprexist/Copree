"""上下文顺序与空闲判定：一次「答上一条」漂移事故的根因回归

2026-09-25 实测：群里 @ 了 AI，它却回了**上一条**消息。两层原因：

1. 群/私聊历史按「新 → 旧」注入，而压缩按「旧 → 新」实现「保留最后 N 条」——
   于是它保留了最旧的 20 条、把最新的（含触发消息）压进摘要，AI 看不到当轮消息；
2. 空闲判定把内容里的「北京时间」当 UTC 裸时间比——只要上海时刻 > 当前 UTC 时刻
   （东八区，一天里大半时间成立）就判成「未来」、退一年兜底 → 变成「一年前」，
   于是每轮都触发压缩（用户：「可是没有 12h 啊」）。
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")


def _stamp(delta: timedelta) -> str:
    return (datetime.now(TZ) - delta).strftime("[Shanghai %m-%d %H:%M] 某人: 内容")


def test_split_for_compression_keeps_newest():
    """保留最后 N 条 = 保留**最新的** N 条（列表约定为正序：旧 → 新）"""
    from app.services.memory.context_compression_service import split_for_compression

    messages = [{"role": "system", "content": "rules"}]
    messages += [{"role": "user", "content": f"第 {i} 条"} for i in range(1, 26)]

    start, end = split_for_compression(messages, keep_last_n=20)

    assert start == 1 and end == 6, (start, end)          # 26 条里只压最旧的 5 条
    tail = messages[end:]
    assert len(tail) == 20, len(tail)
    assert tail[-1]["content"] == "第 25 条", "最新的一条必须在保留段里"


def test_idle_uses_beijing_time():
    """5 分钟前不算空闲；13 小时前才算（此前时区搞错，恒判「一年前」）"""
    from app.ai.executor import _is_conversation_idle

    assert _is_conversation_idle([{"role": "user", "content": _stamp(timedelta(minutes=5))}]) is False
    assert _is_conversation_idle([{"role": "user", "content": _stamp(timedelta(hours=13))}]) is True


def test_idle_span_rule_still_fires():
    """跨度超过 12 小时（首条 13 小时前、最后一条 1 小时前）按设计也算空闲"""
    from app.ai.executor import _is_conversation_idle

    span = [
        {"role": "user", "content": _stamp(timedelta(hours=14))},
        {"role": "user", "content": _stamp(timedelta(hours=1))},
    ]
    assert _is_conversation_idle(span) is True
