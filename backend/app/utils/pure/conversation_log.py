"""对话日志的保留策略 —— 一段状态留多少，只看它多久没动过。

日志按「状态」（AI 当时的栈顶帧身份）分桶看：一段状态才看得出上下文长什么样。
保留也按状态算，一条规则管到底（不再有「最多的那段」这种特殊待遇——谁活跃谁留满，
规则可预测，也不会出现「别的状态话多，把这段挤没」）：

    3 天内动过   → 20 条（活跃；这个数可被 per-AI / 全局配置覆盖）
    3 ~ 30 天    → 5 条（沉寂）
    超过 30 天   → 2 条（老旧）

判定是纯函数，service 只做 IO。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


IDLE_DAYS = 3                # 多久没动静算「沉寂」
AGED_DAYS = 30               # 多久没动静算「老旧」
ACTIVE_KEEP = 20             # 活跃状态留多少（可被 per-AI / 全局配置覆盖）
IDLE_KEEP = 5                # 沉寂状态留多少
AGED_KEEP = 2                # 老旧状态留多少


@dataclass(frozen=True)
class LogRetention:
    """保留策略（值对象）：只有 active_keep 可配，其余是常量口径。"""

    active_keep: int = ACTIVE_KEEP
    idle_keep: int = IDLE_KEEP
    aged_keep: int = AGED_KEEP
    idle_days: int = IDLE_DAYS
    aged_days: int = AGED_DAYS

    def keep_for(self, *, age_days: float) -> int:
        """这段状态该留几条（唯一判定处）：age_days = 它最后一条日志距今多少天"""
        if age_days >= self.aged_days:
            return self.aged_keep
        if age_days >= self.idle_days:
            return self.idle_keep
        return self.active_keep


def plan_log_trim(rows, retention: LogRetention, *, now: datetime) -> list[int]:
    """算出该删哪些日志 id（唯一实现，纯函数）。

    rows：同一 AI 的 [{id, state_key, created_at}]，**按 created_at 倒序**（新 → 旧）；
    按 state_key 分桶，桶内第一条就是最新那条（老化看它）。每桶只留最新 N 条。
    """
    buckets: dict[str, list] = {}
    for row in rows or []:
        buckets.setdefault(row.get("state_key") or "", []).append(row)
    if not buckets:
        return []
    drop: list[int] = []
    for items in buckets.values():
        keep = retention.keep_for(age_days=_age_days(items[0], now))
        drop.extend(int(row["id"]) for row in items[keep:])
    return drop


def cap_per_state(items, key, cap: int | None):
    """按 state_key 给每个桶封顶（列表接口用；cap=None 表示不封顶，顺序不变）。"""
    if not cap or cap <= 0:
        return list(items)
    seen: dict[str, int] = {}
    kept = []
    for item in items:
        bucket = key(item)
        used = seen.get(bucket, 0)
        if used >= cap:
            continue
        seen[bucket] = used + 1
        kept.append(item)
    return kept


def _age_days(newest, now: datetime) -> float:
    """桶里最新那条距今多少天（时间戳两种写法都认；认不出当 0 = 按活跃处理）。"""
    value = (newest or {}).get("created_at")
    if value is None:
        return 0.0
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return 0.0
    if value.tzinfo is not None:
        value = value.replace(tzinfo=None)
    return max(0.0, (now - value).total_seconds() / 86400)