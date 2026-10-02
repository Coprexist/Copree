"""系统监控「历史趋势」的读侧聚合。

agent_metrics 每分钟落一行原始快照，时间窗一拉长行数就上千：7 天近万行原样发给前端，
recharts 要为每个点生成一段路径，实测曲线 d 属性四十万字符、主线程光算路径就 2.4 秒。
趋势本不需要这么细，所以在服务端先聚合——按采样点等距分组归并，点数收敛到
TIMELINE_MAX_POINTS 以内。

只输出曲线要用的标量。willingness / errors 是「某一刻的分布」，那是 live 快照的职责，
放进趋势既没有读者、又白占五分之一体积（7 天实测 321KB / 1596KB）。
"""
from __future__ import annotations

from typing import Any, Iterable

# 趋势点数上界：曲线画在 1440px 宽的图上也就一千像素出头，
# 360 个点已经是「一像素一个点」的三倍，再多只是让浏览器白算路径。
TIMELINE_MAX_POINTS = 360


def _point(created_at, snapshot_data: dict) -> dict:
    """一行原始快照 → 趋势上的一个点（字段提取只在这里做，别处不再各写一遍）"""
    llm = snapshot_data.get("llm") or {}
    return {
        "at": created_at.isoformat() if created_at else None,
        "llm_calls": llm.get("total_calls", 0),
        "llm_avg_latency": (llm.get("latency") or {}).get("avg", 0),
        "llm_error_rate": llm.get("error_rate", 0),
        "messages_per_second": (snapshot_data.get("messages") or {}).get("per_second_last_60s", 0),
        "queue_depth": (snapshot_data.get("queue") or {}).get("max_depth", 0),
    }


def _weighted_avg(points: list, key: str) -> float:
    """延迟与错误率是「每分钟的均值」，归并要按该分钟的调用数加权：
    否则闲时的 3 秒会跟忙时的 3 秒同权，曲线看不出真实变化。
    整桶都没调用时没有样本可加权，退回普通均值（此时率本就是 0）。"""
    weight = sum(p["llm_calls"] for p in points)
    if weight <= 0:
        return sum(p[key] for p in points) / len(points)
    return sum(p[key] * p["llm_calls"] for p in points) / weight


def _merge(points: list) -> dict:
    """桶内归并：计数器求和、速率与延迟加权、队列取峰值"""
    return {
        "at": points[0]["at"],
        "llm_calls": sum(p["llm_calls"] for p in points),
        "llm_avg_latency": round(_weighted_avg(points, "llm_avg_latency"), 4),
        "llm_error_rate": round(_weighted_avg(points, "llm_error_rate"), 4),
        "messages_per_second": round(
            sum(p["messages_per_second"] for p in points) / len(points), 2
        ),
        "queue_depth": max(p["queue_depth"] for p in points),
    }


def build_timeline(snapshots: Iterable[Any], max_points: int = TIMELINE_MAX_POINTS) -> list:
    """窗口内的原始快照 → 不超过 max_points 个趋势点（按时间升序传入）。

    采样本身是等间隔的（flush worker 每 60s 一行），所以按索引等分等价于按时间等分，
    还省掉解析时间戳；桶的时间戳取桶内第一个采样点，不假装自己有更细的分辨率。
    分桶按比例取边界，桶大小只差一个，点数恰好等于 min(点数, 上界)——先算桶宽再取整会
    出现「1000 个点、上界 999、结果只剩 500」这种腰斩。
    """
    points = [_point(s.created_at, s.snapshot_data or {}) for s in snapshots]
    total = len(points)
    keep = min(total, max_points)
    if keep <= 0:
        return []
    return [
        _merge(points[i * total // keep:(i + 1) * total // keep])
        for i in range(keep)
    ]
