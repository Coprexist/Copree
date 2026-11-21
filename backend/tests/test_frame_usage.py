"""帧预算的纯算术：小时 / 天桶、跨桶归零、上限归一。

「一次」= 一次 LLM API 调用；桶按固定边界切（小时整点、天按展示时区），
不需要定时任务（见 app/utils/pure/frame_usage.py）。
"""
from datetime import datetime, timedelta, timezone

from app.utils.pure import frame_usage as fu

# 2026-11-01 03:20 UTC = 11:20 Asia/Shanghai
NOW = datetime(2026, 11, 1, 3, 20, tzinfo=timezone.utc)


def test_buckets_accumulate_within_the_same_hour():
    usage = fu.bump(None, 2, now=NOW)
    assert fu.count(usage, "hour", now=NOW) == 2
    usage = fu.bump(usage, 1, now=NOW + timedelta(minutes=10))
    assert fu.count(usage, "hour", now=NOW + timedelta(minutes=10)) == 3


def test_hour_bucket_rolls_over_without_a_scheduler():
    usage = fu.bump(None, 5, now=NOW)
    later = NOW + timedelta(hours=1, minutes=1)
    assert fu.count(usage, "hour", now=later) == 0
    rolled = fu.bump(usage, 1, now=later)
    assert fu.count(rolled, "hour", now=later) == 1


def test_day_bucket_uses_the_display_timezone():
    usage = fu.bump(None, 1, now=NOW)
    assert fu.count(usage, "day", now=NOW) == 1
    # 同一天晚些时候仍算今天；跨到第二天归零
    assert fu.count(usage, "day", now=NOW + timedelta(hours=8)) == 1
    assert fu.count(usage, "day", now=NOW + timedelta(days=1, hours=1)) == 0


def test_limits_are_normalized():
    assert fu.limit("") == 0
    assert fu.limit("abc") == 0
    assert fu.limit(-3) == 0
    assert fu.limit("12") == 12
    assert fu.round_limit(0) == fu.DEFAULT_ROUND_LIMIT
    assert fu.round_limit(None) == fu.DEFAULT_ROUND_LIMIT
    assert fu.round_limit("12") == 12


def test_ratio_never_divides_by_zero():
    assert fu.ratio(5, 0) == 0.0
    assert fu.ratio(5, 10) == 0.5


def test_describe_skips_unlimited_slots():
    # 只配了一轮上限：小时 / 天 / token 都不念「不限」，省字数也省读的人一句废话
    assert fu.describe(50) == "本轮上限 50 次"
    line = fu.describe(12, hour_used=3, hour_limit=20, day_used=8, day_limit=100,
                       token_percent=0.42)
    assert line == "本轮上限 12 次 · 这一小时 3/20 次 · 今天 8/100 次 · token 已用 42%"


def test_describe_carries_the_suggested_session_length():
    """建议单次时长只是建议：进那一行给 AI 看，不当成上限用（0 = 不提）"""
    assert fu.describe(50, session_minutes=0) == "本轮上限 50 次"
    assert fu.describe(50, session_minutes=20) == "本轮上限 50 次 · 建议单次 20 分钟"
