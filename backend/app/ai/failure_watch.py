"""连续失败看门狗：把「悄悄哑火」变成一条能看见的告警。

为什么要有这个：
2026-11-15 私信整条链路因为一个漏 import 的 NameError 全线哑火 40 分钟。异常在
response_worker 里只变成一行 logger.error —— 界面没有红点、监控没有指标、日志没人盯着，
最后是用户来问「我的 AI 怎么不回我」。

这里只做一件事：按 agent 记连续失败次数，跨过阈值吐一条告警，之后按冷却窗口抑制重复，
成功一次就清零。为什么放在内存：它是运行期健康状态，不是业务数据；进程重启后重新计数即可，
写进数据库反而要多一张表和一次清理。
"""
from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass(frozen=True)
class FailureAlert:
    """一次升级告警：谁、失败了几轮、跨了多久、最后一条错误是什么。"""

    agent_id: int
    failures: int
    duration_seconds: float
    last_error: str


@dataclass
class _Bucket:
    failures: int = 0
    first_at: float = 0.0
    last_error: str = ""
    alerted_at: float = 0.0


class FailureWatch:
    """按 agent 统计连续失败；只有「刚跨过阈值」或「冷却结束」才返回告警。

    阈值与冷却都是构造参数，单测里直接给一个小窗口即可，不用睡真实时间。
    """

    def __init__(self, threshold: int = 3, cooldown_seconds: float = 1800.0):
        if threshold < 1:
            raise ValueError("threshold 至少为 1")
        self.threshold = threshold
        self.cooldown_seconds = cooldown_seconds
        self._buckets: dict[int, _Bucket] = {}

    def record_failure(self, agent_id: int, error: object, *, now: float | None = None) -> FailureAlert | None:
        """记一次失败；跨阈值（且不在冷却里）时返回告警，否则 None。"""
        at = time.time() if now is None else now
        bucket = self._buckets.get(agent_id)
        if bucket is None:
            bucket = _Bucket(first_at=at)
            self._buckets[agent_id] = bucket
        bucket.failures += 1
        bucket.last_error = (str(error) or error.__class__.__name__)[:300]
        if bucket.failures < self.threshold:
            return None
        if bucket.alerted_at and at - bucket.alerted_at < self.cooldown_seconds:
            return None
        bucket.alerted_at = at
        return FailureAlert(
            agent_id=agent_id,
            failures=bucket.failures,
            duration_seconds=round(at - bucket.first_at, 1),
            last_error=bucket.last_error,
        )

    def record_success(self, agent_id: int) -> None:
        """成功一次就清零：连续失败的定义是「中间没有成功过」。"""
        self._buckets.pop(agent_id, None)

    def failures_for(self, agent_id: int) -> int:
        bucket = self._buckets.get(agent_id)
        return bucket.failures if bucket else 0


# 全进程一个实例：worker 是单循环，各 agent 的计数互不影响
failure_watch = FailureWatch()
