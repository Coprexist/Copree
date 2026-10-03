"""
API Key 并发管理器

追踪每个池 Key 的实时飞行中请求数，支持：
- acquire/release 并发槽位
- 429 自动标记冷却
- 选择负载最低的 Key
- 导出统计供监控

纯内存、asyncio.Lock 保护。进程重启自动归零（正确行为：无请求在处理中）。
"""
import asyncio
import logging
import time

logger = logging.getLogger(__name__)

# 默认并发限制（按模型名后缀匹配）
DEFAULT_CONCURRENT_LIMITS = {
    "flash": 2500,  # deepseek-flash（V4.1）
    "pro": 500,     # deepseek-v4-pro
}
FALLBACK_CONCURRENT_LIMIT = 500  # 未知模型默认
DEFAULT_RATE_LIMIT_COOLDOWN = 60  # 429 冷却秒数


class ApiKeyConcurrencyManager:
    """单例：API Key 并发追踪器"""

    def __init__(self):
        self._concurrency: dict[int, int] = {}  # pool_key_id → 当前飞行中请求数
        self._rate_limited_until: dict[int, float] = {}  # pool_key_id → 冷却结束时间戳 (time.monotonic)
        self._lock = asyncio.Lock()

    def _get_default_limit(self, model: str, db_limit: int | None = None) -> int:
        """根据模型名和 DB 配置返回并发上限"""
        if db_limit is not None:
            return db_limit
        model_lower = model.lower()
        for suffix, limit in DEFAULT_CONCURRENT_LIMITS.items():
            if suffix in model_lower:
                return limit
        return FALLBACK_CONCURRENT_LIMIT

    async def acquire(self, pool_key_id: int, model: str = "", db_limit: int | None = None) -> bool:
        """
        尝试获取一个并发槽位。成功返回 True，超限或冷却中返回 False。
        """
        async with self._lock:
            # 检查是否在 429 冷却期
            cooldown = self._rate_limited_until.get(pool_key_id, 0)
            now = time.monotonic()
            if cooldown > now:
                remaining = int(cooldown - now)
                logger.debug(f"  Key #{pool_key_id} 冷却中（剩余 {remaining}s），跳过")
                return False

            current = self._concurrency.get(pool_key_id, 0)
            limit = self._get_default_limit(model, db_limit)
            if current >= limit:
                logger.debug(f"  Key #{pool_key_id} 并发已满 ({current}/{limit})")
                return False

            self._concurrency[pool_key_id] = current + 1
            return True

    async def release(self, pool_key_id: int):
        """释放一个并发槽位"""
        async with self._lock:
            current = self._concurrency.get(pool_key_id, 0)
            if current > 0:
                self._concurrency[pool_key_id] = current - 1

    async def mark_rate_limited(self, pool_key_id: int, cooldown_seconds: float = DEFAULT_RATE_LIMIT_COOLDOWN):
        """标记 Key 被 429 限流，进入冷却期"""
        async with self._lock:
            until = time.monotonic() + cooldown_seconds
            self._rate_limited_until[pool_key_id] = until
            logger.warning(
                f"  🚫 API Key 池 #{pool_key_id} 被限流 {cooldown_seconds}s "
                f"(恢复时间: {time.strftime('%H:%M:%S', time.localtime(until))})"
            )

    async def get_least_loaded(self, keys: list, model: str = "") -> int | None:
        """
        从可用 Key 列表中返回当前负载最低（且未冷却、未超限）的 Key ID。
        keys 为 ApiKeyPool ORM 对象列表。
        """
        async with self._lock:
            best_id = None
            best_load = float('inf')
            now = time.monotonic()

            for key in keys:
                # 跳过 429 冷却中的 Key
                if self._rate_limited_until.get(key.id, 0) > now:
                    continue

                db_limit = getattr(key, 'concurrent_limit', None)
                limit = self._get_default_limit(model, db_limit)
                load = self._concurrency.get(key.id, 0)

                # 跳过已满的 Key
                if load >= limit:
                    continue

                if load < best_load:
                    best_load = load
                    best_id = key.id

            return best_id

    def get_stats(self) -> dict:
        """导出统计信息（供 /admin/metrics）"""
        now = time.monotonic()
        cooldown_keys = [
            {"pool_key_id": kid, "remaining_seconds": int(v - now)}
            for kid, v in self._rate_limited_until.items()
            if v > now
        ]
        return {
            "concurrency": dict(self._concurrency),
            "rate_limited": cooldown_keys,
            "total_in_flight": sum(self._concurrency.values()),
        }

    def get_current_load(self, pool_key_id: int) -> int:
        """获取单个 Key 的当前并发数（不加锁，用于快速查询）"""
        return self._concurrency.get(pool_key_id, 0)


# 全局单例
concurrency_mgr = ApiKeyConcurrencyManager()
