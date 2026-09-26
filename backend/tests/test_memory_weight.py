"""记忆权值：两个权值合成有效权重，决定注入 / 藏着 / 物理删除

设计见 docs/memory_system/design/focus_and_memory_reach.md 第七节。锁住三件事：
- 30 天 / 200 次调用就是半衰期的字面含义（时间权值 0.5）；
- 真实时间与调用次数取较快的一个——一天里聊掉几百轮，旧事一样退场；
- 退场不等于删除：只有设定权值最低的那一档走物理删除，其余留着可显式召回。
"""
from datetime import datetime, timedelta, timezone


def _now() -> datetime:
    return datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)


def test_half_life_is_literal():
    from app.utils.pure.memory_weight import time_weight

    assert time_weight(days=30) == 0.5
    assert time_weight(days=90) == 0.25
    assert time_weight(calls=200) == 0.5, "200 次调用与 30 天等效"


def test_subjective_time_takes_the_faster_axis():
    from app.utils.pure.memory_weight import subjective_days

    assert subjective_days(1, 400) == 60.0, "一天里 400 次调用 = 主观 60 天"
    assert subjective_days(90, 0) == 90.0, "没人说话时按真实时间走"


def test_lowest_weight_dies_within_a_single_busy_day():
    from app.utils.pure.memory_weight import disposition_at

    assert disposition_at(1, _now(), 0, _now(), 400) == "delete"


def test_exiting_is_not_always_deleting():
    from app.utils.pure.memory_weight import disposition_at

    # 权值 2 与 5 各自静默到阈值线上：退场，但记录保留
    assert disposition_at(2, _now() - timedelta(days=90), 0, _now(), 0) == "hidden"
    assert disposition_at(5, _now() - timedelta(days=270), 0, _now(), 0) == "hidden"


def test_fresh_memory_is_injected():
    from app.utils.pure.memory_weight import disposition_at

    assert disposition_at(1, _now(), 0, _now(), 0) == "inject"
    assert disposition_at(5, _now(), 0, _now(), 300) == "inject", "权值高的，聊得多也还在"


def test_baseline_is_the_last_recall_not_creation():
    from app.utils.pure.memory_weight import silence

    assert silence(_now() - timedelta(days=5), 100, _now(), 300) == (5.0, 200)
    assert silence(None, None, _now(), 300) == (0.0, 0), "还没有基准时从当下起算"


def test_weight_is_clamped_and_typed():
    from app.utils.pure.memory_weight import clamp_weight, default_weight

    assert clamp_weight(9) == 5 and clamp_weight(0) == 1
    assert clamp_weight(None) == 3
    assert default_weight("person") == 5 and default_weight("daily") == 1
    assert default_weight("nonexistent") == 3
