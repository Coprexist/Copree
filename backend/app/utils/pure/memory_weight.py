"""
记忆权值纯函数 — 无 IO、无 DB 依赖。

一条记忆有两个权值：
- 设定权值：写入时由 AI 给定（1-5），此后不变；
- 时间权值：读取时现算，不落库。

两者合成有效权重，决定这条记忆此刻的处境（照常注入 / 藏着不注入 / 该物理删除）。

时间尺度用「主观时长」：真实静默时间与静默期间的 API 调用次数换算后取较快的一个——
聊得多，主观上就过了更久，旧事该退场。衰减取反比例而非指数：0.5^x 是恒定半衰期，
会「突然就没了」；反比例前期掉得快、后期长尾，更接近遗忘曲线，且永不归零，
正好交给阈值来淘汰。
"""
from __future__ import annotations

from datetime import datetime

from app.utils.pure.timeutil import as_utc

# ── 量纲 ──
WEIGHT_MIN = 1
WEIGHT_MAX = 5              # 设定权值上限，也是有效权重归一化的分母
INJECT_THRESHOLD = 0.1      # 有效权重低于它就不再自动注入
DELETE_MAX_WEIGHT = 1       # 只有设定权值不高于它的记忆才物理删除（节约空间）

# ── 时间尺度 ──
HALF_LIFE_DAYS = 30.0       # 静默 30 天
HALF_LIFE_CALLS = 200       # 或静默期间发生 200 次 API 调用，两者等效

# ── 类型（情感陪伴定位，不照搬工程 agent 的分类）──
MEMORY_TYPES = ("person", "relationship", "promise", "event", "preference", "daily")
TYPE_DEFAULT_WEIGHT = {
    "person": 5,          # 是谁、什么关系
    "relationship": 5,    # 关系变化
    "promise": 4,         # 答应过什么
    "event": 3,           # 共同经历
    "preference": 3,      # 偏好
    "daily": 1,           # 日常流水
}
DEFAULT_WEIGHT = 3        # 未知类型按「一般」


def clamp_weight(weight) -> int:
    """把设定权值夹到 1-5；非法值按「一般」处理。"""
    try:
        value = int(weight)
    except (TypeError, ValueError):
        return DEFAULT_WEIGHT
    return max(WEIGHT_MIN, min(WEIGHT_MAX, value))


def default_weight(mem_type: str | None) -> int:
    """类型的默认设定权值（未知类型按「一般」）。"""
    return TYPE_DEFAULT_WEIGHT.get((mem_type or "").strip(), DEFAULT_WEIGHT)


def subjective_days(days: float, calls: int) -> float:
    """主观静默时长：真实天数与调用次数折合的等效天数，取较快的那一个。"""
    by_time = max(0.0, float(days or 0.0))
    by_calls = max(0, int(calls or 0)) * HALF_LIFE_DAYS / HALF_LIFE_CALLS
    return max(by_time, by_calls)


def time_weight(days: float = 0.0, calls: int = 0) -> float:
    """时间权值 = 1 / (1 + 主观时长/半衰期)：静默满一个半衰期时衰减一半。"""
    return 1.0 / (1.0 + subjective_days(days, calls) / HALF_LIFE_DAYS)


def effective_weight(weight: int, days: float = 0.0, calls: int = 0) -> float:
    """有效权重 = (设定权值 / 上限) × 时间权值，落在 (0, 1]。"""
    return (clamp_weight(weight) / WEIGHT_MAX) * time_weight(days, calls)


def disposition(weight: int, eff: float) -> str:
    """有效权重决定一条记忆此刻的处境。

    inject：照常参与注入与召回；
    hidden：不再自动注入，记录保留，显式召回仍可取回；
    delete：设定权值本就最低，物理删除，不占空间。

    不高于阈值即退场：阈值恰好落在「权值 1 静默一个半衰期」的交点上，
    所以最低那一档的退场时刻就是它满一个半衰期的时候。
    """
    if eff > INJECT_THRESHOLD:
        return "inject"
    return "delete" if clamp_weight(weight) <= DELETE_MAX_WEIGHT else "hidden"


def silence(
    last_touched_at: datetime | None,
    last_touched_call: int | None,
    now: datetime,
    call_count: int,
) -> tuple[float, int]:
    """距最近一次被想起的静默时长（天、API 调用次数）。

    基准取「最近一次被召回」而不是创建时间：常用的记忆自然活得久，
    而设定权值一个字不改。从未有过基准（新写入或旧数据）则以当下起算，
    等第一次被召回才开始计时。
    """
    if last_touched_at is None:
        return 0.0, 0
    days = max(0.0, (as_utc(now) - as_utc(last_touched_at)).total_seconds() / 86400.0)
    calls = max(0, int(call_count or 0) - int(last_touched_call or 0))
    return days, calls


def disposition_at(
    weight: int,
    last_touched_at: datetime | None,
    last_touched_call: int | None,
    now: datetime,
    call_count: int,
) -> str:
    """按时间基准现算处境（调用方的唯一入口，免得各自拼 silence + disposition）。"""
    days, calls = silence(last_touched_at, last_touched_call, now, call_count)
    return disposition(weight, effective_weight(weight, days, calls))
