"""容量准入：硬上限、优先级、背压不丢、AIMD 收敛。

测的是**闸的行为**，不是某套部署的阈值——阈值全部来自配置（见 docs/dev/capacity.md），
所以在 1 个 AI 与 10 万个 AI 的部署里这些都是同一套断言。
"""
import asyncio

import pytest

from app.services.infrastructure import capacity

pytestmark = pytest.mark.anyio


class FakeLag:
    def __init__(self, lag: float = 0.0) -> None:
        self.lag = lag

    def p95(self) -> float:
        return self.lag


def _inflight(adm: "capacity.Admission") -> int:
    return adm.snapshot()["inflight"]


async def test_hard_limit_is_never_exceeded():
    adm = capacity.Admission()
    adm.configure(hard=2)
    peak = 0

    async def turn():
        nonlocal peak
        async with adm.slot():
            peak = max(peak, _inflight(adm))
            await asyncio.sleep(0.01)

    await asyncio.gather(*(turn() for _ in range(6)))

    snap = adm.snapshot()
    assert peak == 2, peak                      # 同时最多两个
    assert snap["inflight"] == 0 and snap["peak"] == 2
    assert snap["admitted"] == 6                # 六个都跑完了，没有丢


async def test_zero_hard_is_unlimited_and_never_queues():
    """hard=0 是"交给部署层的其它手段"：这里退化成计数器，不排队、不建 future。"""
    adm = capacity.Admission()
    adm.configure(hard=0)
    peak = 0

    async def turn():
        nonlocal peak
        async with adm.slot():
            peak = max(peak, _inflight(adm))
            await asyncio.sleep(0.01)

    await asyncio.gather(*(turn() for _ in range(20)))

    assert adm.limit == 0
    assert peak == 20
    assert adm.snapshot()["queued_total"] == 0
    adm.adjust(3)                               # 不限时没有"目标值"可言
    assert adm.limit == 0


async def test_high_priority_is_served_before_queued_normal():
    """@我 的回合不能排在群里十句闲聊后面——上限越小越要紧。"""
    adm = capacity.Admission()
    adm.configure(hard=1)
    order: list[str] = []
    release = asyncio.Event()

    async def holder():
        async with adm.slot():
            order.append("holder")
            await release.wait()

    async def waiter(name: str, priority: str):
        async with adm.slot(priority):
            order.append(name)

    held = asyncio.create_task(holder())
    await asyncio.sleep(0.01)
    normals = [asyncio.create_task(waiter(f"n{i}", capacity.NORMAL)) for i in range(3)]
    await asyncio.sleep(0.01)
    high = asyncio.create_task(waiter("high", capacity.HIGH))
    await asyncio.sleep(0.01)

    assert adm.snapshot()["waiting"] == {capacity.HIGH: 1, capacity.NORMAL: 3}
    release.set()
    await asyncio.gather(held, high, *normals)

    assert order[0] == "holder" and order[1] == "high", order
    assert order[2:] == ["n0", "n1", "n2"], order      # 同类仍然 FIFO


async def test_cancelled_waiter_leaves_no_leaked_slot():
    """等待者被取消：名额不许漏，队列也不许卡住。"""
    adm = capacity.Admission()
    adm.configure(hard=1)
    release = asyncio.Event()

    async def holder():
        async with adm.slot():
            await release.wait()

    async def waiter():
        async with adm.slot():
            pass

    held = asyncio.create_task(holder())
    await asyncio.sleep(0.01)
    first = asyncio.create_task(waiter())
    second = asyncio.create_task(waiter())
    await asyncio.sleep(0.01)
    assert adm.snapshot()["waiting"][capacity.NORMAL] == 2

    first.cancel()
    await asyncio.sleep(0.01)
    assert adm.snapshot()["waiting"][capacity.NORMAL] == 1

    release.set()
    await asyncio.gather(held, second)
    assert _inflight(adm) == 0

    async with adm.slot():          # 还进得去，说明没有名额被漏掉
        assert _inflight(adm) == 1
    assert _inflight(adm) == 0


async def test_raising_target_wakes_waiters():
    adm = capacity.Admission()
    adm.configure(hard=4)
    adm.adjust(1)                   # 先模拟一次收缩：目标值 1
    release = asyncio.Event()
    entered = asyncio.Event()

    async def turn(name: str):
        async with adm.slot():
            if name == "second":
                entered.set()
            await release.wait()

    first = asyncio.create_task(turn("first"))
    await asyncio.sleep(0.01)
    second = asyncio.create_task(turn("second"))
    await asyncio.sleep(0.01)
    assert not entered.is_set()

    adm.adjust(2)                   # 目标值调大：等在队列里的人立刻能进
    await asyncio.wait_for(entered.wait(), timeout=1)

    release.set()
    await asyncio.gather(first, second)


def test_aimd_halves_under_pressure_and_recovers_within_bounds():
    adm = capacity.Admission()
    adm.configure(hard=16, floor=2)
    gov = capacity.Governor(adm, FakeLag())
    gov.enabled = True              # 增长只在开了自适应时发生（收缩永远生效）

    gov.pressure = lambda: "测试压力"
    gov.step(now=0.0)
    assert adm.target == 8, adm.target
    gov.step(now=1.0)               # 冷却期内：一次压力只应对一次
    assert adm.target == 8, adm.target
    gov.step(now=100.0)
    assert adm.target == 4, adm.target

    gov.pressure = lambda: ""
    gov.step(now=2.0)               # 刚收缩过：冷却期内不许反弹
    assert adm.target == 4, adm.target

    gov.step(now=10_000.0)          # 冷静够了：加一
    assert adm.target == 5, adm.target
    for _ in range(50):
        gov.step(now=10_000.0)
    assert adm.target == 16, "自适应不许越过硬上限"

    gov.pressure = lambda: "又压力"
    for i in range(20):
        gov.step(now=30_000.0 + i * 1000)     # 每步都越过冷却：才会一次次减半
    assert adm.target == 2, "也不许低于下限"


def test_governor_does_nothing_when_unlimited():
    adm = capacity.Admission()
    adm.configure(hard=0)
    gov = capacity.Governor(adm, FakeLag())
    gov.pressure = lambda: "压力"
    gov.step(now=0.0)
    assert adm.target == 0          # 不限就没有可调的东西


def test_negative_setting_disables_the_gate():
    """部署方想彻底关掉时得有个明确写法（-1），不能只能靠"设一个很大的数"。"""
    from app.config import settings

    cap = capacity
    cap._configured = False
    original = settings.ai_max_concurrent_turns
    settings.ai_max_concurrent_turns = -1
    try:
        cap.configure_from_settings()
        assert cap.admission.hard == 0 and cap.admission.limit == 0
    finally:
        settings.ai_max_concurrent_turns = original
        cap._configured = False
        cap.admission.configure(hard=0)


def test_configure_from_settings_is_idempotent():
    """bootstrap 与 worker 都会装配：第二次不许把收敛中的目标值打回硬上限。"""
    cap = capacity
    cap._configured = False
    try:
        cap.configure_from_settings()
        hard = cap.admission.hard
        if not hard:                     # SQLite 之类读不到池子：这里没东西可测
            return
        cap.admission.adjust(max(1, hard // 2))
        settled = cap.admission.target
        cap.configure_from_settings()
        assert cap.admission.target == settled, "重复装配把目标值重置了"
    finally:
        cap._configured = False
        cap.admission.configure(hard=0)


async def test_snapshot_is_published_to_metrics():
    """面板看得到：读数要真的进 metrics 单例，不是只躺在模块里。"""
    from app.services.infrastructure.metrics_collector import metrics

    cap = capacity
    cap._configured = False
    try:
        cap.configure_from_settings()
        cap.publish_snapshot()
        live = await metrics.snapshot()
        assert live["capacity"]["admission"]["hard"] == cap.admission.hard
        assert set(live["capacity"]) == {"admission", "loop_lag", "pool", "memory",
                                         "queue_depth", "governor"}
    finally:
        cap._configured = False
        cap.admission.configure(hard=0)


def test_snapshot_shape_is_stable():
    """观测字段是给人看面板用的，形状不能随手变。"""
    adm = capacity.Admission()
    adm.configure(hard=4, floor=2)
    snap = adm.snapshot()
    assert set(snap) == {"hard", "target", "limit", "inflight", "peak", "admitted",
                         "queued_total", "slow_waits", "waited_seconds", "waiting",
                         "waiting_ms", "last_wait_ms"}


def _stub_memory(capacity_module, used_mb: float):
    """把内存读数换成固定值（返回原始 staticmethod 供还原）。"""
    original = capacity_module.Signals.__dict__["memory"]
    setattr(capacity_module.Signals, "memory",
            staticmethod(lambda: {"used_mb": used_mb, "source": "test"}))
    return original


def test_admin_memory_limit_shrinks_even_without_adaptive_switch():
    """管理员写下"内存上限"就是"别越这条"：不开自适应也得生效。"""
    cap = capacity
    cap._policy.set(0, 0)
    adm = cap.Admission()
    adm.configure(hard=16, floor=1)
    gov = cap.Governor(adm, FakeLag())
    gov.pressure = lambda: ""                      # 系统自身没压力

    original = _stub_memory(cap, 780.0)             # 780/800 = 97.5%
    try:
        cap.apply_admin_policy(0, 800)
        gov.step(now=0.0)
        assert adm.target == 8, adm.target

        setattr(cap.Signals, "memory",
                staticmethod(lambda: {"used_mb": 900.0, "source": "test"}))
        gov.step(now=100.0)                         # 已超上限且冷却已过：继续减半
        assert adm.target == 4, adm.target
    finally:
        setattr(cap.Signals, "memory", original)
        cap.apply_admin_policy(0, 0)


def test_daily_target_holds_instead_of_creeping_up():
    """贴着日常水位就停住：既不缩也不涨（哪怕自适应开着）。"""
    cap = capacity
    cap._policy.set(0, 0)
    adm = cap.Admission()
    adm.configure(hard=16, floor=1)
    gov = cap.Governor(adm, FakeLag())
    gov.enabled = True
    gov.pressure = lambda: ""

    original = _stub_memory(cap, 900.0)             # 超过日常目标 800
    try:
        cap.apply_admin_policy(800, 0)
        gov.step(now=0.0)
        assert adm.target == 8, adm.target

        setattr(cap.Signals, "memory",
                staticmethod(lambda: {"used_mb": 700.0, "source": "test"}))  # 87.5%
        gov.step(now=100_000.0)                     # 冷却也过了，但仍在水位附近
        assert adm.target == 8, "接近日常目标时不该再涨"
    finally:
        setattr(cap.Signals, "memory", original)
        cap.apply_admin_policy(0, 0)
        gov.enabled = False


def test_unset_memory_policy_ignores_memory_entirely():
    """没填就完全忽略内存：走默认三信号（这是"不填也没关系"的保底）。"""
    cap = capacity
    cap._policy.set(0, 0)
    original = _stub_memory(cap, 99_999.0)
    try:
        assert cap._policy.verdict() == ("ok", "")
    finally:
        setattr(cap.Signals, "memory", original)


def test_memory_policy_validates_order_at_the_policy_level():
    cap = capacity
    try:
        cap.apply_admin_policy(2048, 1024)          # 日常 2G > 上限 1G
        assert cap._policy.daily_mb == 2048 and cap._policy.limit_mb == 1024
        # 取值本身不管顺序（服务层拒绝这种组合），但判定必须先看上限——
        # 否则"日常更高"会让它永远停在超过上限的状态里
        original = _stub_memory(cap, 1500.0)
        try:
            kind, reason = cap._policy.verdict()
            assert kind == "shrink" and "上限" in reason, (kind, reason)
        finally:
            setattr(cap.Signals, "memory", original)
    finally:
        cap.apply_admin_policy(0, 0)



async def test_queue_wait_is_measured_and_fast_path_is_not():
    """排队时长要量出来（协调遗漏的反面）；没排队的不许塞 0 稀释分位。"""
    adm = capacity.Admission()
    adm.configure(hard=1)
    release = asyncio.Event()
    waited = None

    async def holder():
        async with adm.slot():
            await release.wait()

    async def waiter():
        nonlocal waited
        async with adm.slot() as queued:
            waited = queued

    held = asyncio.create_task(holder())
    await asyncio.sleep(0.01)
    queued_task = asyncio.create_task(waiter())
    await asyncio.sleep(0.06)
    release.set()
    await asyncio.gather(held, queued_task)

    assert waited and waited > 0.03, waited
    snap = adm.snapshot()
    assert snap["waiting_ms"]["count"] == 1, snap["waiting_ms"]
    assert snap["waiting_ms"]["p50"] >= 30, snap["waiting_ms"]
    assert snap["waited_seconds"] >= 0.03, snap["waited_seconds"]

    async with adm.slot() as fast:
        assert fast == 0.0
    assert adm.snapshot()["waiting_ms"]["count"] == 1, "快路径不该产生排队样本"


async def test_turn_latency_is_published():
    """每轮端到端时长（含排队）进指标：这是"用户等了多久"的唯一来源。"""
    from app.services.infrastructure.metrics_collector import metrics

    await metrics.record_turn(2.0, 0.5)
    live = await metrics.snapshot()
    rounds = live["rounds"]
    assert rounds["count"] >= 1
    assert rounds["latency"]["count"] >= 1
    assert rounds["queue"]["p95"] >= 0.5, rounds["queue"]



def test_queue_pressure_outranks_loop_lag():
    """主信号换成"用户在等"：排队超过预算就收缩，即使事件循环很健康。"""
    cap = capacity
    adm = cap.Admission()
    adm.configure(hard=16, floor=1)
    gov = cap.Governor(adm, FakeLag(0.0))      # 事件循环延迟为 0
    gov.pressure = cap.Governor.pressure.__get__(gov)   # 用真实实现
    gov.queue_lag_budget = 2.0

    adm._wait_times.append((__import__("time").monotonic(), 5.0))   # 刚排了 5 秒
    assert "排队" in gov.pressure(), gov.pressure()
    gov.step(now=0.0)
    assert adm.target == 8, adm.target


def test_stale_queue_samples_do_not_keep_pressing():
    """十分钟前的排队不能算成"现在还在排队"，否则会一直缩到下限。"""
    cap = capacity
    admin = cap.Admission()
    admin.configure(hard=16, floor=1)
    now = __import__("time").monotonic()
    admin._wait_times.append((now - 600.0, 9.0))

    assert admin.recent_p95_ms() == 0.0
    # 同样的样本，站在"当时"的视角就在窗口内：窗口口径确实按时间走
    assert admin.recent_p95_ms(now=now - 599) > 0
    gov = cap.Governor(admin, FakeLag(0.0))
    gov.queue_lag_budget = 2.0
    assert gov.pressure() == "", gov.pressure()

