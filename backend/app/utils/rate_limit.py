"""进程内限流原语：滑动窗口计数 + 连续失败锁定。

只用标准库、不落库。当前部署是 uvicorn 单进程（docker-compose 里没开 --workers），
进程内状态就够用；代价是重启即清空——防爆破场景可以接受（攻击者也得从头计数），
而真要跨进程/跨重启就该换 Redis 或落库，那时只换这两个类的实现，调用方不动。

键来自外部输入（用户名、来源 IP），所以两个类都做容量上限 + 惰性清理：
不设上界等于给攻击者一个往进程里塞键的口子。
"""
from __future__ import annotations

import math
import time
from collections import OrderedDict, deque

MAX_KEYS = 4096     # 每个实例最多记这么多键，超了丢最久没碰过的


def _seconds_until(deadline: float, now: float) -> int:
    """距离 deadline 还有几秒：0 = 已经没有等待，否则向上取整且至少 1 秒。

    对外只说整秒（Retry-After 是秒），0 专表示「不用等」，所以不足一秒也算 1。
    """
    return 0 if deadline <= now else max(1, math.ceil(deadline - now))


class SlidingWindow:
    """滑动窗口限流：一个 key 在 window 秒内最多 limit 次。"""

    def __init__(self, limit: int, window: float, *, max_keys: int = MAX_KEYS) -> None:
        self.limit = max(int(limit), 1)
        self.window = float(window)
        self._max_keys = max(int(max_keys), 1)
        self._hits: OrderedDict[str, deque[float]] = OrderedDict()

    def hit(self, key: str) -> int:
        """记一次访问：返回 0 = 放行；>0 = 已超限，需要等待的秒数。"""
        now = time.monotonic()
        bucket = self._bucket(key, now)
        while bucket and now - bucket[0] > self.window:
            bucket.popleft()
        if len(bucket) >= self.limit:
            # 窗口内最早那次滑出去就腾出一个名额，那一刻就是下一次能成功的时间。
            # 不写死常量：窗口改了它也自动跟着变。
            return _seconds_until(bucket[0] + self.window, now)
        bucket.append(now)
        return 0

    def clear(self, key: str) -> None:
        self._hits.pop(key, None)

    def __len__(self) -> int:
        return len(self._hits)

    def _bucket(self, key: str, now: float) -> deque[float]:
        bucket = self._hits.get(key)
        if bucket is None:
            if len(self._hits) >= self._max_keys:
                self._hits.popitem(last=False)
            bucket = deque()
            self._hits[key] = bucket
        else:
            self._hits.move_to_end(key)
        return bucket


class FailureGuard:
    """连续失败锁定：窗口内累计到 threshold 次就锁 lock_seconds。

    解锁后再犯按次数翻倍（上限 max_lock_seconds）——手动解封或短锁到期后继续打，
    代价要肉眼可见地增长。成功一次调 clear()：这是「连续」失败的口径。
    """

    def __init__(self, threshold: int, lock_seconds: float, *, window: float = 900.0,
                 max_lock_seconds: float = 3600.0, max_keys: int = MAX_KEYS) -> None:
        self.threshold = max(int(threshold), 1)
        self.lock_seconds = float(lock_seconds)
        self.window = float(window)
        self.max_lock_seconds = float(max_lock_seconds)
        self._max_keys = max(int(max_keys), 1)
        self._state: OrderedDict[str, dict] = OrderedDict()

    def retry_after(self, key: str) -> int:
        """还在锁定期内吗：返回需要等待的秒数（0 = 没锁）"""
        state = self._state.get(key)
        if state is None:
            return 0
        self._state.move_to_end(key)
        return _seconds_until(state["locked_until"], time.monotonic())

    def next_lock_seconds(self, key: str) -> float:
        """下一次触发时的锁定时长（按已犯次数翻倍）——测试与排查要看它"""
        state = self._state.get(key)
        strikes = int(state["strikes"]) if state else 0
        return min(self.lock_seconds * (2 ** strikes), self.max_lock_seconds)

    def record(self, key: str) -> int:
        """记一次失败：返回 0 = 还没到阈值；>0 = 已锁定，剩余秒数。"""
        now = time.monotonic()
        state = self._state.get(key)
        if state is None:
            if len(self._state) >= self._max_keys:
                self._state.popitem(last=False)
            state = {"fails": deque(), "locked_until": 0.0, "strikes": 0}
            self._state[key] = state
        else:
            self._state.move_to_end(key)
        locked = _seconds_until(state["locked_until"], now)
        if locked:
            return locked                      # 锁定期内继续失败不再叠加惩罚
        fails = state["fails"]
        while fails and now - fails[0] > self.window:
            fails.popleft()
        fails.append(now)
        if len(fails) < self.threshold:
            return 0
        lock = self.next_lock_seconds(key)
        state["strikes"] += 1
        state["locked_until"] = now + lock
        fails.clear()
        return max(1, math.ceil(lock))

    def clear(self, key: str) -> None:
        """清零：计数与累计次数都归位（成功登录后对账号这么做）"""
        self._state.pop(key, None)

    def __len__(self) -> int:
        return len(self._state)
