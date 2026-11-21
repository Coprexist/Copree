"""容量准入：本进程的"同时在跑几个回合"预算 + 可选的自适应收敛。

为什么需要它：现在并发只在**群**这一级限（`chat_chain` 每群一个信号量），没有进程内的
总量。一个回合要占一份提示词（几万 token）和一到几条数据库连接；群一多，一起进来的回合
就把连接池和事件循环挤满了。挤满之后的表现不是报错，是"所有群一起变慢、状态停在思考中"
——2026-11-07 那次 12 小时冻结虽然根因在数据库锁，但形态一模一样。

**三条边界，写死在设计里：**

1. **阈值不来自观察。** 上限、下限、压力阈值全部来自配置。代码里没有任何"我们线上大概是
   多少"的数字——不同部署相差四个数量级（一个开发者部署 1 个 AI，也可能有 10 万个），
   任何来自某一套实例的经验值对另一套都是错的。唯一的推导是从**连接池配置**推
   （`pool_size + max_overflow`）：那是部署方自己给的硬资源，也是排队真正会落到的位置。
2. **与实例数无关。** 额度是**本进程**的，不是"全世界"的：
   - 1 个 AI：`hard=0`（不限）或默认跟随池容量，这里退化成一次整数加减，不排队、不加锁；
   - 1 万个 AI：单进程本就是错误形态，那时按分片部署，每个后端进程各管自己这一片的额度，
     业务代码不需要知道有多个进程；
   - 10 万 / 100 万 AI：那是"分片 + 外部队列 + 多副本"的形态，准入闸仍然是每进程一道，
     跨进程的额度得由部署层的配额（Redis / 网关 / 租户配额）来做——本模块不假装能当全局配额。
3. **自动收敛永不越过硬上限，且默认关。** 它的作用不是"猜出合适值"，而是部署方给了宽上限
   时别一次打满。算法是 AIMD（TCP 拥塞控制那一套）：没压力慢慢加，一有压力立刻减半再冷静
   一会儿。收敛快、不因一次抖动永久缩水，也不需要知道正确值（我们并不知道）。

一个入口：`async with admission.slot(priority):` —— 只有"会并发派发的回合"需要它。
私信与闹钟是单消费者串行 `await` 的，天然最多一个，不需要第二道闸（见 docs/dev/capacity.md）。
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from contextlib import asynccontextmanager
from typing import Deque, Dict

logger = logging.getLogger(__name__)

# 只有两档：有人在等着（@我 / 群助手被点名）与群里路过的一句。
# 不做更多档——档位一多，"谁该先"就成了需要解释的规则，而实现只需要保证高优先级不被淹没。
HIGH = "high"
NORMAL = "normal"
_PRIORITIES = (HIGH, NORMAL)


# ============================================================
# 准入
# ============================================================

class Admission:
    """本进程的并发准入：`limit` 以内直接进，超出排队（按优先级 FIFO）。"""

    def __init__(self) -> None:
        self.hard = 0          # 硬上限：0 = 不限。configure() 之后不再改（来自配置）
        self.target = 0        # 当前目标：自适应只动它，且 [floor, hard]
        self.floor = 1
        self._inflight = 0
        self._peak = 0
        self._admitted = 0
        self._queued_total = 0
        self._slow_waits = 0
        self._waited_seconds = 0.0
        self._last_wait = 0.0
        # 排队样本 (monotonic 时刻, 秒)：只记"真排过队"的（快路径不记，否则分位被一堆 0 淹没），
        # 且带时间戳——十分钟没排队时，旧样本不该继续被当成"现在还在排队"。
        self._wait_times: Deque[tuple[float, float]] = deque(maxlen=256)
        self._waiters: Dict[str, Deque[asyncio.Future]] = {p: deque() for p in _PRIORITIES}

    # ── 配置 ──

    def configure(self, hard: int, floor: int = 1) -> None:
        """部署方给的额度；`hard <= 0` 表示不限（部署方自己用其它手段兜底）。"""
        self.hard = max(0, int(hard or 0))
        self.floor = max(1, min(int(floor or 1), self.hard or 1))
        self.target = self.hard
        logger.info("容量准入：硬上限=%s，下限=%s（0 表示不限）", self.hard or "不限", self.floor)
        self._wake()

    @property
    def limit(self) -> int:
        """当前生效的上限：0 表示不限。"""
        if not self.hard:
            return 0
        return max(1, min(self.target or self.hard, self.hard))

    def adjust(self, target: int) -> None:
        """只给 governor 用：把目标值夹到 [floor, hard] 并唤醒能进的人。"""
        if not self.hard:
            return
        capped = max(self.floor, min(int(target), self.hard))
        if capped == self.target:
            return
        before, self.target = self.target, capped
        logger.info("容量目标 %s → %s（硬上限 %s）", before, capped, self.hard)
        if capped > before:
            self._wake()

    # ── 进出 ──

    def _can_take(self) -> bool:
        limit = self.limit
        return limit == 0 or self._inflight < limit

    def _take(self) -> None:
        self._inflight += 1
        self._peak = max(self._peak, self._inflight)

    async def acquire(self, priority: str = NORMAL) -> float:
        """拿一个名额；返回在队列里等了多久（秒，没排队就是 0）。"""
        if self._can_take():
            self._take()
            self._admitted += 1
            return 0.0

        waiting_since = time.monotonic()
        self._queued_total += 1
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._waiters[priority if priority in self._waiters else NORMAL].append(fut)
        try:
            await fut
        except asyncio.CancelledError:
            # 等待者被取消：把它从队列摘掉；已经放行了的名额要还回去，否则名额会漏
            for queue in self._waiters.values():
                try:
                    queue.remove(fut)
                except ValueError:
                    pass
            if fut.done() and not fut.cancelled():
                self.release()
            raise
        waited = time.monotonic() - waiting_since
        self._waited_seconds += waited
        self._last_wait = waited
        self._wait_times.append((waiting_since, waited))
        if waited > SLOW_WAIT_SECONDS:
            self._slow_waits += 1
            logger.warning("回合排队 %.1fs 才拿到名额（inflight=%s limit=%s）", waited,
                           self._inflight, self.limit)
        return waited

    def release(self) -> None:
        if self._inflight > 0:
            self._inflight -= 1
        self._wake()

    def _wake(self) -> None:
        """按优先级放行：先 high，后 normal，同类 FIFO。"""
        while self._can_take():
            fut = self._pop_waiter()
            if fut is None:
                return
            self._take()
            self._admitted += 1
            fut.set_result(True)

    def _pop_waiter(self):
        for priority in _PRIORITIES:
            queue = self._waiters[priority]
            while queue:
                fut = queue.popleft()
                if not fut.done():      # 取消掉的残骸跳过
                    return fut
        return None

    @asynccontextmanager
    async def slot(self, priority: str = NORMAL):
        """`async with admission.slot(HIGH) as waited:` —— 拿不到名额就等，不丢回合。

        `waited` 是这次在队列里等了多久（秒）：调用方拿它记账，
        "用户到底等了多久"才不会被"只统计执行时间"掩盖（coordinated omission）。
        """
        waited = await self.acquire(priority)
        try:
            yield waited
        finally:
            self.release()

    # ── 观测 ──

    def recent(self, window: float = 60.0, now: float | None = None) -> list[float]:
        """最近 window 秒内的排队时长（秒）。样本过期就不算——"现在还在排队"才该触发压力。"""
        now = time.monotonic() if now is None else now
        cutoff = now - window
        return [waited for at, waited in self._wait_times if at >= cutoff]

    def recent_p95_ms(self, window: float = 60.0, now: float | None = None) -> float:
        samples = self.recent(window, now)
        if not samples:
            return 0.0
        ordered = sorted(samples)
        return ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))] * 1000

    def snapshot(self) -> dict:
        return {
            "hard": self.hard,          # 0 = 不限
            "target": self.target,
            "limit": self.limit,
            "inflight": self._inflight,
            "peak": self._peak,
            "admitted": self._admitted,
            "queued_total": self._queued_total,
            "slow_waits": self._slow_waits,
            "waited_seconds": round(self._waited_seconds, 1),
            "waiting": {p: len(q) for p, q in self._waiters.items()},
            "waiting_ms": _percentiles_ms(self.recent()),
            "last_wait_ms": int(round(self._last_wait * 1000)),
        }


def _percentiles_ms(samples) -> dict:
    """排队时长分位（毫秒）。空窗口返回 0，形状保持稳定，面板不用判空。"""
    if not samples:
        return {"count": 0, "p50": 0, "p95": 0, "max": 0}
    ordered = sorted(samples)
    def at(ratio: float) -> int:
        return int(round(ordered[min(len(ordered) - 1, int(len(ordered) * ratio))] * 1000))
    return {"count": len(ordered), "p50": at(0.5), "p95": at(0.95),
            "max": int(round(ordered[-1] * 1000))}


SLOW_WAIT_SECONDS = 30.0     # 排队超过这么久就记一条 warning（不是超时，背压就是不丢）


# ============================================================
# 压力信号：都只读
# ============================================================

class Signals:
    """事件循环延迟 / 连接池占用 / 入口队列深度。读不到就当没有（SQLite 没有池）。"""

    @staticmethod
    def pool() -> dict:
        try:
            from app.database import engine

            pool = getattr(engine, "pool", None)
            if pool is None:
                return {}
            size = int(getattr(pool, "size", lambda: 0)() or 0)
            overflow = int(getattr(pool, "_max_overflow", 0) or 0)
            checked = int(getattr(pool, "checkedout", lambda: 0)() or 0)
            return {"size": size, "max_overflow": overflow,
                    "checked_out": checked, "capacity": size + overflow}
        except Exception:  # noqa: BLE001 —— 观测失败不该影响准入
            return {}

    @staticmethod
    def memory() -> dict:
        """这个服务占多少内存：优先 cgroup（含 chromium、常驻世界等子进程）。

        管理员看 `docker stats` 是多少，这里就该是多少——只报单进程 RSS 会让人对不上账。
        裸机直跑没有 cgroup 时退回自己的 RSS。
        """
        used = None
        source = ""
        for path, name in (("/sys/fs/cgroup/memory.current", "cgroup2"),
                           ("/sys/fs/cgroup/memory/memory.usage_in_bytes", "cgroup1")):
            try:
                with open(path) as fh:
                    used = int(fh.read().strip()) / 1048576
                source = name
                break
            except Exception:  # noqa: BLE001 —— 没有这个文件就走下一条
                continue
        if used is None:
            try:
                with open("/proc/self/statm") as fh:
                    used = int(fh.read().split()[1]) * 4096 / 1048576
                source = "rss"
            except Exception:  # noqa: BLE001
                return {}
        return {"used_mb": round(used, 1), "source": source,
                "daily_target_mb": _policy.daily_mb, "limit_mb": _policy.limit_mb}

    @staticmethod
    def queue_depth() -> int:
        try:
            from app.services.infrastructure.metrics_collector import metrics

            return int(getattr(metrics, "current_queue_depth", 0) or 0)
        except Exception:  # noqa: BLE001
            return 0


# 两条线相对于"用户设的那条线"的比例：不是经验值，是水位线的通用取法
# （90% 就算快到了；接近日常目标 85% 就停止试探增长，别顶着走）。
LIMIT_SOFT_RATIO = 0.9
DAILY_COMFORT_RATIO = 0.85


class MemoryPolicy:
    """管理员在设置里给的两条内存线（MB，0 = 没设）。

    "向用户设置倾斜"的意思很具体：他写下的线排在默认阈值之前——命中就直接收缩，
    不看冷却、也不看其它信号；没设就完全忽略内存，走默认算法（延迟 / 池子 / 队列）。
    """

    def __init__(self) -> None:
        self.daily_mb = 0
        self.limit_mb = 0

    def set(self, daily_mb, limit_mb) -> tuple[int, int]:
        self.daily_mb = max(0, int(daily_mb or 0))
        self.limit_mb = max(0, int(limit_mb or 0))
        return self.daily_mb, self.limit_mb

    @property
    def active(self) -> bool:
        return bool(self.daily_mb or self.limit_mb)

    def verdict(self) -> tuple[str, str]:
        """(shrink|hold|ok, 原因)。读不到内存就当没设。"""
        if not self.active:
            return "ok", ""
        used = float(Signals.memory().get("used_mb") or 0)
        if not used:
            return "ok", ""
        if self.limit_mb and used >= self.limit_mb:
            return "shrink", f"内存 {used:.0f}MB 已达上限 {self.limit_mb}MB"
        if self.limit_mb and used >= self.limit_mb * LIMIT_SOFT_RATIO:
            return "shrink", f"内存 {used:.0f}MB 接近上限 {self.limit_mb}MB"
        if self.daily_mb and used >= self.daily_mb:
            return "shrink", f"内存 {used:.0f}MB 超过日常目标 {self.daily_mb}MB"
        if self.daily_mb and used >= self.daily_mb * DAILY_COMFORT_RATIO:
            return "hold", f"内存 {used:.0f}MB 接近日常目标 {self.daily_mb}MB"
        return "ok", ""


_policy = MemoryPolicy()


class LoopLag:
    """事件循环调度延迟：单进程里最诚实的"我忙不过来"。

    每 `interval` 秒睡一次，实际睡了多久多出来的部分就是这一段的积压。
    比 RSS 可靠：Python 的内存还给 OS 很慢，看它会一路加到底；延迟不会撒谎。
    """

    def __init__(self, *, interval: float = 1.0, window: int = 60) -> None:
        self._interval = interval
        self._samples: Deque[float] = deque(maxlen=window)
        self._task: asyncio.Task | None = None

    async def _loop(self) -> None:
        while True:
            started = time.monotonic()
            await asyncio.sleep(self._interval)
            self._samples.append(max(0.0, time.monotonic() - started - self._interval))

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="capacity-loop-lag")

    def p95(self) -> float:
        if not self._samples:
            return 0.0
        ordered = sorted(self._samples)
        return ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]

    def snapshot(self) -> dict:
        return {"p95_ms": round(self.p95() * 1000, 1),
                "samples": len(self._samples)}


# ============================================================
# 自适应（AIMD）
# ============================================================

class Governor:
    """在 [floor, hard] 之间按 AIMD 收敛目标值；默认关（只采样观察）。"""

    def __init__(self, admission: Admission, lag: LoopLag, *, settings=None) -> None:
        self._admission = admission
        self._lag = lag
        self._settings = settings
        self.enabled = False
        self.interval = 10.0
        self.cooldown = 60.0
        self.queue_lag_budget = 2.0  # 秒：排队时长（最近 60 秒 p95）
        self.lag_budget = 0.25       # 秒：事件循环延迟（兜底信号）
        self.pool_ratio = 0.9
        self.queue_ratio = 0.8
        self.queue_capacity = 500    # response_worker 的 message_queue 上限
        self._cooldown_until = 0.0
        self._last_reason = ""
        self._changes = 0
        self._floor_warned = False

    def configure(self, settings) -> None:
        self.enabled = bool(getattr(settings, "ai_adaptive_concurrency", False))
        self.queue_lag_budget = max(
            0.01, float(getattr(settings, "ai_pressure_queue_lag_ms", 2000))) / 1000
        self.lag_budget = max(0.01, float(getattr(settings, "ai_pressure_loop_lag_ms", 250))) / 1000
        self.pool_ratio = float(getattr(settings, "ai_pressure_pool_ratio", 0.9))
        self.queue_ratio = float(getattr(settings, "ai_pressure_queue_ratio", 0.8))
        self._last_reason = ""
        self._changes = 0
        self._cooldown_until = 0.0
        self._floor_warned = False

    def verdict(self) -> tuple[str, str]:
        """(shrink|hold|ok, 原因)：管理员给的内存线优先，然后是系统自身的三个信号。"""
        kind, reason = _policy.verdict()
        if kind != "ok":
            return kind, reason
        hit = self.pressure()
        return ("shrink", hit) if hit else ("ok", "")

    def pressure(self) -> str:
        """返回压力原因（空串 = 没压力）。三条都只看"现在是不是快满了"。

        第一条最要紧：**排队时间**。它是"用户真的在等"的直接代理（Netflix Vegas / Envoy
        Gradient 也是用延迟估队列，而不是看 CPU/内存）；事件循环延迟退到第二条当兜底，
        因为它会被无关的慢操作污染（比如某个插件出网卡了 3 秒）。
        """
        queued_ms = self._admission.recent_p95_ms()
        if queued_ms > self.queue_lag_budget * 1000:
            return f"排队 {queued_ms:.0f}ms > {self.queue_lag_budget * 1000:.0f}ms"
        lag = self._lag.p95()
        if lag > self.lag_budget:
            return f"事件循环延迟 {lag * 1000:.0f}ms > {self.lag_budget * 1000:.0f}ms"
        pool = Signals.pool()
        capacity = int(pool.get("capacity") or 0)
        if capacity and int(pool.get("checked_out") or 0) >= self.pool_ratio * capacity:
            return f"连接池占用 {pool['checked_out']}/{capacity}"
        depth = Signals.queue_depth()
        if depth and depth >= self.queue_ratio * self.queue_capacity:
            return f"入口队列 {depth}/{self.queue_capacity}"
        return ""

    def step(self, now: float | None = None) -> None:
        """一次决策。抽出来是为了能单独测：不依赖真实时钟与真实压力。"""
        now = time.monotonic() if now is None else now
        if not self._admission.hard:
            return
        kind, reason = self.verdict()
        if kind == "hold":
            # 贴着管理员给的日常水位：不缩也不涨，停在这儿
            self._last_reason = reason
            return
        if kind == "shrink":
            if now < self._cooldown_until:
                # 一次压力只应对一次：否则超限状态下每 10 秒减半一次，一分钟就掉到 1
                return
            before = self._admission.target
            self._admission.adjust(before // 2)
            self._cooldown_until = now + self.cooldown
            if self._admission.target != before:
                self._changes += 1
                logger.warning("容量收缩 → %s（%s）", self._admission.target, reason)
            if self._admission.target <= self._admission.floor:
                if not self._floor_warned:
                    self._floor_warned = True
                    logger.error(
                        "容量已收缩到下限 %s：管理员设的内存线可能低于本服务的基础占用"
                        "（当前 %sMB，%s）。请调大或留空，否则会一直贴着下限跑。",
                        self._admission.floor, Signals.memory().get("used_mb"), reason)
            else:
                self._floor_warned = False
            self._last_reason = reason
            return
        if not self.enabled:
            # 没开自适应：默认算法只负责"别炸"（收缩），不主动试探增长
            return
        if now < self._cooldown_until:
            return                      # 刚收缩过：冷静一会儿再往上试
        before = self._admission.target
        self._admission.adjust(min(self._admission.hard, before + 1))
        if self._admission.target != before:
            self._changes += 1
            self._last_reason = "无压力，试探性加一"

    async def run(self) -> None:
        ticks = 0
        while True:
            await asyncio.sleep(self.interval)
            ticks += 1
            try:
                # 管理员设了内存线就一定要算：那是他明确的"别越这条"，
                # 和"要不要主动试探增长"（enabled）是两件事。
                if self.enabled or _policy.active:
                    self.step()
                if ticks % 6 == 0:
                    # 兜底刷新：有人绕过接口直接改库也能在一分钟内跟上
                    await refresh_policy()
            except Exception as e:  # noqa: BLE001 —— 收敛失败只该少调一次，不该拖垮进程
                logger.warning("容量自适应这一步失败（沿用当前目标）: %s", e)
            publish_snapshot()

    def snapshot(self) -> dict:
        return {
            "enabled": self.enabled,
            "queue_lag_budget_ms": round(self.queue_lag_budget * 1000, 1),
            "loop_lag_budget_ms": round(self.lag_budget * 1000, 1),
            "pool_ratio": self.pool_ratio,
            "queue_ratio": self.queue_ratio,
            "changes": self._changes,
            "last_reason": self._last_reason,
            "cooling": time.monotonic() < self._cooldown_until,
        }


# ============================================================
# 单例与装配
# ============================================================

admission = Admission()
loop_lag = LoopLag()
governor = Governor(admission, loop_lag)
_configured = False


def apply_admin_policy(daily_mb, limit_mb) -> None:
    """管理员保存设置时直接推过来：不重启、不等下一轮轮询。"""
    before = (_policy.daily_mb, _policy.limit_mb)
    daily, limit = _policy.set(daily_mb, limit_mb)
    if before != (daily, limit):
        logger.info("容量策略：日常内存目标=%sMB，上限=%sMB（0 = 未设，走默认算法）",
                    daily or "未设", limit or "未设")


async def refresh_policy() -> None:
    """从库里读一次管理员设置（启动时与之后每分钟）。读不到就沿用上一次。"""
    try:
        from app.database import async_session
        from app.services.infrastructure.system_settings_service import get_settings

        async with async_session() as db:
            row = await get_settings(db)
        apply_admin_policy(row.get("memory_daily_target_mb"), row.get("memory_limit_mb"))
    except Exception as e:  # noqa: BLE001 —— 策略刷新失败不该影响准入
        logger.debug("容量策略刷新失败（沿用上一次）: %s", e)


def configure_from_settings() -> None:
    """启动时读一次配置。上限没配（0）就跟随连接池容量——那是部署方给的硬资源。

    幂等：bootstrap 先调一次（从第一刻就有闸），capacity_worker 里再调也不会把
    governor 正在收敛的目标值打回硬上限。
    """
    global _configured
    if _configured:
        return
    from app.config import settings

    raw = int(getattr(settings, "ai_max_concurrent_turns", 0) or 0)
    if raw < 0:
        hard = 0                   # 显式关掉（部署方用别的机制兜底）
    elif raw == 0:
        hard = int(Signals.pool().get("capacity") or 0)   # 跟随池子；读不到（SQLite）= 不限
    else:
        hard = raw
    admission.configure(hard, floor=int(getattr(settings, "ai_adaptive_floor", 1) or 1))
    governor.configure(settings)
    _configured = True


def snapshot() -> dict:
    return {
        "admission": admission.snapshot(),
        "loop_lag": loop_lag.snapshot(),
        "pool": Signals.pool(),
        "memory": Signals.memory(),
        "queue_depth": Signals.queue_depth(),
        "governor": governor.snapshot(),
    }


def publish_snapshot() -> None:
    """把读数推给指标单例，管理台/指标接口就能看到。"""
    try:
        from app.services.infrastructure.metrics_collector import metrics

        metrics.capacity = snapshot()
    except Exception:  # noqa: BLE001
        pass


async def capacity_worker() -> None:
    """启动入口（bootstrap 的 spawn_task）：装配 → 开始采样 → 每秒/十秒各自跑。"""
    configure_from_settings()
    loop_lag.start()
    await refresh_policy()
    publish_snapshot()
    await governor.run()
