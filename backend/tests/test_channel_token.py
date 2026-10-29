"""通道对话额度：计费口径与剩余量（纯函数，不碰 DB）

这些数是三处共用的：卡片上的剩余、拦截那一刻的判定、记账时累加的量。
口径一旦分叉，就会出现"面板说还剩 3 万、对话却说已用完"，所以钉在纯函数这一层。
"""
import pytest


def test_meter_modes_and_normalization():
    from app.utils.pure import channel_token as ct

    assert ct.normalize_mode("TOTAL") == "total"
    assert ct.normalize_mode("billable") == "billable"
    assert ct.normalize_mode("whatever") == ct.DEFAULT_METER_MODE == "billable"
    assert ct.normalize_limit(None) == ct.DEFAULT_LIMIT_TOKENS == 1_000_000
    assert ct.normalize_limit("1500000") == 1_500_000
    assert ct.normalize_limit(-5) == 0


def test_charged_tokens_two_modes():
    from app.utils.pure import channel_token as ct

    usage = {"prompt_tokens": 100, "cached_tokens": 80, "completion_tokens": 20, "total_tokens": 120}
    # total：全部 token（含缓存命中）
    assert ct.charged_tokens("total", usage) == 120
    # billable：未命中的输入（100-80）+ 输出
    assert ct.charged_tokens("billable", usage) == 40
    # 老接口不回 total_tokens 时按两项之和兜底
    assert ct.charged_tokens("total", {"prompt_tokens": 7, "completion_tokens": 3}) == 10
    # 缓存数比 prompt 还大只可能是上游字段错位：按 0 计，不记成负数
    assert ct.charged_tokens("billable", {"prompt_tokens": 10, "cached_tokens": 99, "completion_tokens": 5}) == 5


def test_remaining_and_exhausted():
    from app.utils.pure import channel_token as ct

    assert ct.remaining(1000, 900) == 100
    assert ct.remaining(1000, 1200) == 0
    assert ct.exhausted(1000, 1000) is True
    assert ct.exhausted(1000, 999) is False
    # 换口径不丢历史：两个计数器都留着，按当前口径取其中一个
    assert ct.used_tokens("total", 500, 300) == 500
    assert ct.used_tokens("billable", 500, 300) == 300


def test_blocked_reply_text_and_conversation_key():
    from app.utils.pure import channel_token as ct

    assert ct.blocked_reply_text("") == ct.DEFAULT_BLOCKED_REPLY
    assert ct.blocked_reply_text("   ") == ct.DEFAULT_BLOCKED_REPLY
    assert ct.blocked_reply_text("额度用完啦") == "额度用完啦"
    assert ct.conversation_key("group", 73) == "group:73"
    assert ct.conversation_key("dm", "5_9") == "dm:5_9"


def test_notice_interval_is_throttled_by_definition():
    from app.utils.pure import channel_token as ct

    # 节流间隔是"同一个对话多久最多提醒一次"，不应该被随手改成 0（那会变成刷屏）
    assert ct.BLOCKED_NOTICE_INTERVAL_SECONDS >= 60
