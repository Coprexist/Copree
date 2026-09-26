"""
轻量级内存指标收集器（单例模式）

设计目的：
  提供系统运行时的性能指标，替代"所有统计靠 SQL 实时聚合"的现状。

指标类别：
  - LLM: 调用延迟(p50/p95/p99)、调用次数、错误率
  - 工具: 按工具名的延迟、成功/失败次数
  - 消息: 总吞吐量、每秒消息数、按 AI 分布
  - 队列: 当前深度、最大深度
  - 意愿: 评分分布直方图
  - 记忆: 批量写入延迟、写入次数

生命周期：
  - 内存操作（热路径开销 < 0.1ms）
  - 每 60 秒异步 flush 到 agent_metrics 表
  - 自动清理超过 retention_days 的旧记录
"""
import asyncio
import time
import random
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List
from app.utils.pure.timeutil import utc_now

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════
# 延迟统计工具
# ══════════════════════════════════════════════════════════════

@dataclass
class LatencyStats:
    """延迟统计（保留最近 N 个样本，计算分位数）"""
    values: List[float] = field(default_factory=list)
    max_samples: int = 1000

    def record(self, seconds: float):
        self.values.append(seconds)
        if len(self.values) > self.max_samples:
            self.values = self.values[-self.max_samples:]

    def percentile(self, p: float) -> float:
        if not self.values:
            return 0.0
        sorted_vals = sorted(self.values)
        idx = int(len(sorted_vals) * p / 100)
        return sorted_vals[min(idx, len(sorted_vals) - 1)]

    def to_dict(self) -> dict:
        if not self.values:
            return {"count": 0, "p50": 0, "p95": 0, "p99": 0, "avg": 0}
        return {
            "count": len(self.values),
            "p50": round(self.percentile(50), 4),
            "p95": round(self.percentile(95), 4),
            "p99": round(self.percentile(99), 4),
            "avg": round(sum(self.values) / len(self.values), 4),
        }


# ══════════════════════════════════════════════════════════════
# MetricsCollector 单例
# ══════════════════════════════════════════════════════════════

class MetricsCollector:
    """轻量级内存指标收集器（单例模式）"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        # LLM 调用
        self.llm_latency = LatencyStats()
        self.llm_call_count: int = 0
        self.llm_error_count: int = 0

        # 工具执行
        self.tool_latency: Dict[str, LatencyStats] = defaultdict(LatencyStats)
        self.tool_success_count: Dict[str, int] = defaultdict(int)
        self.tool_error_count: Dict[str, int] = defaultdict(int)

        # 消息吞吐
        self.message_throughput: Dict[int, int] = defaultdict(int)
        self.message_total: int = 0
        self._throughput_window: List[tuple[float, int]] = []

        # 错误率（按类型分）
        self.errors_by_type: Dict[str, int] = defaultdict(int)

        # 队列深度
        self.max_queue_depth: int = 0
        self.current_queue_depth: int = 0

        # 意愿评分分布直方图
        self.willingness_buckets: Dict[str, int] = defaultdict(int)

        # 记忆写入
        self.memory_write_latency = LatencyStats()
        self.memory_write_count: int = 0

        self._lock = asyncio.Lock()

    # ── 记录方法 ──

    async def record_llm_call(self, latency_seconds: float, success: bool):
        async with self._lock:
            self.llm_latency.record(latency_seconds)
            self.llm_call_count += 1
            if not success:
                self.llm_error_count += 1
                self.errors_by_type["llm_error"] += 1

    async def record_tool_call(self, tool_name: str, latency_seconds: float, success: bool):
        async with self._lock:
            self.tool_latency[tool_name].record(latency_seconds)
            if success:
                self.tool_success_count[tool_name] += 1
            else:
                self.tool_error_count[tool_name] += 1
                self.errors_by_type[f"tool_error:{tool_name}"] += 1

    async def record_message(self, agent_id: int):
        async with self._lock:
            self.message_total += 1
            self.message_throughput[agent_id] += 1
            now = time.monotonic()
            self._throughput_window.append((now, agent_id))
            # 清理 60 秒前的记录
            cutoff = now - 60
            self._throughput_window = [x for x in self._throughput_window if x[0] > cutoff]

    async def record_queue_depth(self, depth: int):
        async with self._lock:
            self.current_queue_depth = depth
            if depth > self.max_queue_depth:
                self.max_queue_depth = depth

    async def record_willingness(self, score: int):
        async with self._lock:
            bucket = f"{(score // 10) * 10}-{(score // 10 + 1) * 10}"
            self.willingness_buckets[bucket] += 1

    async def record_memory_write(self, latency_seconds: float):
        async with self._lock:
            self.memory_write_latency.record(latency_seconds)
            self.memory_write_count += 1

    async def record_error(self, error_type: str):
        async with self._lock:
            self.errors_by_type[error_type] += 1

    # ── 快照 + flush ──

    async def snapshot(self) -> dict:
        """获取当前快照（不清空数据）"""
        async with self._lock:
            return {
                "llm": {
                    "latency": self.llm_latency.to_dict(),
                    "total_calls": self.llm_call_count,
                    "error_count": self.llm_error_count,
                    "error_rate": round(self.llm_error_count / max(1, self.llm_call_count), 4),
                },
                "tools": {
                    name: {
                        "latency": stats.to_dict(),
                        "success": self.tool_success_count[name],
                        "error": self.tool_error_count[name],
                    }
                    for name, stats in self.tool_latency.items()
                },
                "messages": {
                    "total": self.message_total,
                    "per_second_last_60s": round(len(self._throughput_window) / 60, 2),
                    "by_agent": dict(self.message_throughput),
                },
                "queue": {
                    "current_depth": self.current_queue_depth,
                    "max_depth": self.max_queue_depth,
                },
                "willingness": dict(self.willingness_buckets),
                "memory": {
                    "latency": self.memory_write_latency.to_dict(),
                    "total_writes": self.memory_write_count,
                },
                "errors": dict(self.errors_by_type),
                "snapshot_at": time.time(),
            }

    async def flush(self) -> dict:
        """获取快照并重置计数器（保留延迟分布）"""
        snapshot = await self.snapshot()

        async with self._lock:
            # 重置计数器，保留 latency stats（维持历史分布）
            self.llm_call_count = 0
            self.llm_error_count = 0
            self.tool_success_count.clear()
            self.tool_error_count.clear()
            self.message_total = 0
            self.message_throughput.clear()
            self._throughput_window.clear()
            self.max_queue_depth = 0
            self.willingness_buckets.clear()
            self.memory_write_count = 0
            self.errors_by_type.clear()

        return snapshot


# 全局单例
metrics = MetricsCollector()


# ══════════════════════════════════════════════════════════════
# 后台 Flush Worker
# ══════════════════════════════════════════════════════════════

async def metrics_flush_worker():
    """
    后台 worker：每 60 秒 flush 指标到 agent_metrics 表。
    并惰性清理超过保留天数的旧记录（5% 概率触发）。
    """
    from app.config import settings

    logger.info(
        "📊 指标收集 flush worker 已启动（间隔=60s, 保留=%d 天）",
        settings.agent_metrics_retention_days,
    )

    while True:
        try:
            await asyncio.sleep(60)
            snapshot = await metrics.flush()

            from app.database import async_session
            from app.models.agent_metrics import AgentMetricsSnapshot

            async with async_session() as db:
                try:
                    record = AgentMetricsSnapshot(snapshot_data=snapshot)
                    db.add(record)
                    await db.commit()
                    logger.debug(
                        "📊 指标已 flush (llm_calls=%d, msgs=%d)",
                        snapshot["llm"]["total_calls"],
                        snapshot["messages"]["total"],
                    )
                except Exception as e:
                    logger.error(f"指标 flush 到 DB 失败: {e}")

                # 惰性清理：5% 概率触发，避免每次 flush 都扫全表
                if random.random() < 0.05:
                    try:
                        from sqlalchemy import text
                        from datetime import datetime, timedelta

                        cutoff = utc_now() - timedelta(
                            days=settings.agent_metrics_retention_days
                        )
                        result = await db.execute(
                            text("DELETE FROM agent_metrics WHERE created_at < :cutoff"),
                            {"cutoff": cutoff},
                        )
                        await db.commit()
                        deleted = result.rowcount
                        if deleted:
                            logger.info("🧹 指标自动清理: 删除 %d 条超过 %d 天的旧记录",
                                       deleted, settings.agent_metrics_retention_days)
                    except Exception as e:
                        logger.warning(f"指标自动清理失败: {e}")

        except asyncio.CancelledError:
            logger.info("指标 flush worker 正在关闭...")
            break
        except Exception as e:
            logger.error(f"指标 flush worker 异常: {e}", exc_info=True)
            await asyncio.sleep(5)
