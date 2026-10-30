"""闹钟唤醒消息：必须带「现在」，以及这条计划是什么时候写的。

线上：凌晨给自己排的「夜里的心跳（下一班）」在第二天下午 14:51 触发。那一轮提示词里没有钟，
模型只能拿账本里历史消息的时间戳当线索，于是照着任务里的「夜里」两个字写了
「夜里这会儿，群安静得像有人把灯调暗了」——任务本身没错，缺的是"现在几点"。
"""
from datetime import datetime, timedelta, timezone

from app.ai.alarm import wake_message, written_at_text


def test_wake_message_states_now_and_when_the_plan_was_written():
    text = wake_message(
        146, "夜里的心跳（下一班）：去群「诗」打一个孔",
        "2026-10-30 14:51 Asia/Shanghai（周五）",
        "2026-10-29 23:22 Asia/Shanghai（周四），约 15.5 小时前",
    )
    assert "**现在：** 2026-10-30 14:51 Asia/Shanghai（周五）" in text
    assert "这条计划是 2026-10-29 23:22" in text
    assert "以**现在**为准" in text
    assert "夜里的心跳（下一班）" in text          # 任务原文照旧交给模型


def test_wake_message_without_written_at_still_states_now():
    text = wake_message(7, "看看群里", "2026-10-30 14:51 Asia/Shanghai（周五）")
    assert "**现在：**" in text
    assert "这条计划是" not in text


def test_written_at_text_reports_local_time_and_elapsed():
    created = datetime.now(timezone.utc) - timedelta(hours=15.5)
    text = written_at_text(created, "Asia/Shanghai")
    assert "Asia/Shanghai" in text
    assert "约 15.5 小时前" in text
