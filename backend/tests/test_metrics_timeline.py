"""系统监控趋势的服务端聚合：点数上界，以及归并时没把量纲弄错。

agent_metrics 每分钟一行，7 天近万行；这里验两件事——点数被收住，
以及收住之后计数器求和、速率与延迟按调用数加权、队列取峰值都还对。
"""
from datetime import datetime, timedelta

import pytest

pytestmark = pytest.mark.anyio


class _Snap:
    """只要 created_at / snapshot_data 两个属性，build_timeline 不依赖 ORM"""

    def __init__(self, created_at, snapshot_data):
        self.created_at = created_at
        self.snapshot_data = snapshot_data


def _snap(minute: int, calls: int, latency: float, rate: float, mps: float, depth: int):
    return _Snap(
        datetime(2026, 1, 1) + timedelta(minutes=minute),
        {
            "llm": {
                "total_calls": calls,
                "error_rate": rate,
                "latency": {"avg": latency, "count": calls},
            },
            "messages": {"per_second_last_60s": mps},
            "queue": {"max_depth": depth},
            "willingness": {"3": 1},
            "errors": {"timeout": 1},
        },
    )


def test_short_window_is_returned_point_by_point():
    from app.services.infrastructure.metrics_timeline import build_timeline

    timeline = build_timeline([
        _snap(0, 3, 1.5, 0.0, 0.1, 2),
        _snap(1, 5, 2.0, 0.2, 0.3, 4),
    ])

    assert [p["at"] for p in timeline] == [
        "2026-01-01T00:00:00", "2026-01-01T00:01:00",
    ]
    assert [p["llm_calls"] for p in timeline] == [3, 5]
    assert timeline[1]["llm_avg_latency"] == 2.0
    assert timeline[1]["queue_depth"] == 4
    assert set(timeline[0]) == {
        "at", "llm_calls", "llm_avg_latency", "llm_error_rate",
        "messages_per_second", "queue_depth",
    }, "趋势只发标量，willingness/errors 属于 live 快照"


def test_long_window_is_bounded_without_distorting_the_axes():
    from app.services.infrastructure.metrics_timeline import build_timeline

    # 6 行、上界 2 → 每桶 3 行，分组完全确定，可以逐项对答案
    rows = [
        _snap(0, 1, 1.0, 1.0, 0.5, 1),
        _snap(1, 1, 1.0, 0.0, 1.5, 3),
        _snap(2, 2, 4.0, 0.5, 1.0, 2),
        _snap(3, 0, 2.0, 0.0, 0.0, 9),
        _snap(4, 0, 4.0, 0.0, 0.0, 9),
        _snap(5, 0, 6.0, 0.0, 0.0, 9),
    ]
    timeline = build_timeline(rows, max_points=2)

    assert len(timeline) == 2
    assert [p["at"] for p in timeline] == ["2026-01-01T00:00:00", "2026-01-01T00:03:00"], \
        "桶的时间戳取桶内第一个采样点"
    assert sum(p["llm_calls"] for p in timeline) == 4, "计数器求和：总量不能因为降采样而变"
    # 桶一：(1*1 + 0*1 + 0.5*2) / 4；桶二整桶无调用，退回普通均值
    assert timeline[0]["llm_error_rate"] == 0.5
    assert timeline[1]["llm_error_rate"] == 0.0
    assert timeline[0]["llm_avg_latency"] == 2.5, "按调用数加权，不是三个数的算术平均 2.0"
    assert timeline[1]["llm_avg_latency"] == 4.0
    assert timeline[0]["messages_per_second"] == 1.0
    assert [p["queue_depth"] for p in timeline] == [3, 9], "队列取峰值，均值会把尖峰抹掉"


def test_point_count_never_exceeds_the_cap():
    from app.services.infrastructure.metrics_timeline import (
        TIMELINE_MAX_POINTS,
        build_timeline,
    )

    rows = [_snap(i, 1, 0.5, 0.0, 0.1, 1) for i in range(1000)]

    assert len(build_timeline(rows)) <= TIMELINE_MAX_POINTS
    assert len(build_timeline(rows, max_points=7)) <= 7
    # 上界只比行数小一点时也要贴着上界，不能因为取整腰斩成一半
    assert len(build_timeline(rows, max_points=999)) == 999
    assert len(build_timeline(rows, max_points=1000)) == 1000
    assert len(build_timeline(rows, max_points=2000)) == 1000, "点数少于上界就原样返回"


def test_empty_and_blank_snapshots_do_not_explode():
    from app.services.infrastructure.metrics_timeline import build_timeline

    assert build_timeline([]) == []

    pointerless = build_timeline([_Snap(None, None)])
    assert pointerless[0]["at"] is None
    assert pointerless[0]["llm_calls"] == 0
    assert pointerless[0]["queue_depth"] == 0
