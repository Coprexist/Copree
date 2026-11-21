"""连续失败看门狗的单元测试（纯内存，不碰数据库）。"""

from app.ai.failure_watch import FailureWatch


def test_alert_only_after_threshold():
    watch = FailureWatch(threshold=3, cooldown_seconds=60)
    assert watch.record_failure(7, "first", now=100) is None
    assert watch.record_failure(7, "second", now=101) is None
    alert = watch.record_failure(7, "boom", now=102)
    assert alert is not None
    assert alert.agent_id == 7
    assert alert.failures == 3
    assert alert.last_error == "boom"
    assert alert.duration_seconds == 2


def test_success_clears_consecutive_failures():
    watch = FailureWatch(threshold=2, cooldown_seconds=60)
    watch.record_failure(1, "x", now=0)
    watch.record_success(1)
    assert watch.failures_for(1) == 0
    assert watch.record_failure(1, "y", now=1) is None  # 重新从 1 开始数


def test_alert_is_suppressed_during_cooldown_then_reappears():
    watch = FailureWatch(threshold=2, cooldown_seconds=10)
    watch.record_failure(3, "a", now=0)
    assert watch.record_failure(3, "b", now=1) is not None
    # 冷却内继续失败不重复告警
    assert watch.record_failure(3, "c", now=5) is None
    # 冷却结束后再失败一次，重新告警
    alert = watch.record_failure(3, "d", now=12)
    assert alert is not None
    assert alert.failures == 4


def test_agents_are_counted_independently():
    watch = FailureWatch(threshold=2, cooldown_seconds=60)
    watch.record_failure(1, "a", now=0)
    watch.record_failure(2, "b", now=0)
    assert watch.failures_for(1) == 1
    assert watch.failures_for(2) == 1
    assert watch.record_failure(1, "c", now=1) is not None
    assert watch.record_failure(2, "d", now=1) is not None
