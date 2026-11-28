"""解析链的**纯判断**部分 —— 不碰数据库、不读配置

这里只有数据结构与一个函数：给一组候选，按顺序取第一个命中。
候选怎么来（查库、解密、场景序列）在 api_config_resolver.py，那是取数壳。

拆开的理由：纯函数可以零 fixture 单测（tests/test_api_config_resolver.py 的
前半部分就不需要任何数据库），而取数壳依赖 SQLAlchemy 会话。混在一个文件里时，
「这条规则要不要连库才能测」得靠人记。

链的次序与语义见 docs/dev/api_config_chain.md §3。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.utils.pure.llm_endpoint import is_local_endpoint

# ── 档位 ──────────────────────────────────────────────────────────────
LEVEL_BOUND = "bound"
LEVEL_POOL = "pool"
LEVEL_SCENARIO = "scenario"   # 第 ③ 格：该场景的有序默认配置序列
LEVEL_DEFAULT = "default"     # 旧全局默认档（已取消，仅兼容旧调用/日志）
LEVEL_FAIL = "fail"

STATUS_HIT = "hit"
STATUS_SKIP = "skip"

# ── 命中 / 跳过原因（界面文案按这个词表查，不要在别处另造一套） ──────
REASON_OK = "ok"
REASON_NO_BIND = "no_bind"           # 没绑配置档
REASON_BAD_BIND = "bad_bind"         # 绑了，但配置档 / 厂商已删或 kind 不符
REASON_NO_KEY = "no_key"             # 这条配置没有 Key，且地址不是本机
REASON_BAD_KEY = "bad_key"           # Key 解不出来（加密密钥换过 / 数据损坏）
REASON_NO_QUOTA = "no_quota"         # 账单人没有可用额度
REASON_NO_MEMBER = "no_member"       # 池里分不到成员
REASON_OWN_KEY_ONLY = "own_key_only"  # 本次请求只允许账单人自己的配置
REASON_NO_DEFAULT = "no_default"     # 用户没设默认配置（旧全局档口径）
REASON_NO_ROUTE = "no_route"         # 该场景没有配置序列
REASON_EXCLUDED = "excluded"         # 上一次已经用这一档失败过
REASON_SCENARIO_OFF = "scenario_off"  # 用户在这个场景里关掉了默认配置兜底
REASON_NOT_CONFIGURED = "not_configured"


@dataclass(frozen=True)
class ChainStep:
    """决策轨迹的一步：这一档试没试、为什么、用了谁的哪条配置"""

    level: str
    status: str
    reason: str
    label: str = ""
    base_url: str = ""
    model: str = ""

    def as_dict(self) -> dict:
        return {
            "level": self.level, "status": self.status, "reason": self.reason,
            "label": self.label, "base_url": self.base_url, "model": self.model,
        }


@dataclass(frozen=True)
class Candidate:
    """一档的候选结果：要么带着整套配置命中，要么带着原因跳过

    reason == REASON_OK 即命中；其余一律跳过。取数壳负责查库与解密，
    候选本身不含任何判断逻辑，单测可以直接构造。
    """

    level: str
    reason: str
    label: str = ""
    base_url: str = ""
    api_key: str | None = None
    chat_model: str | None = None
    work_model: str | None = None
    provider_info: dict = field(default_factory=dict)
    pool_key_id: int | None = None
    models: tuple[str, ...] = ()
    decrypt_failures: tuple[str, ...] = ()

    @property
    def hit(self) -> bool:
        return self.reason == REASON_OK


@dataclass(frozen=True)
class Resolution:
    """这一次请求实际怎么打，以及为什么是它

    source 是事实（① 绑定 / ② 池 / ③ 默认 / none），chain 是决策轨迹。
    界面上的 stepper 与请求本身共用同一个 Resolution —— 这是「显示即实跑」的结构性保证。
    """

    base_url: str
    api_key: str | None
    chat_model: str | None
    work_model: str | None
    source: str                     # bound | pool | default | none
    credit_source: str              # 记账口径：profile | pool_key | user_key | none
    pool_key_id: int | None
    provider_info: dict
    models: list[str]
    thinking_supported: bool
    system_layout: str
    needs_key: bool
    chain: list[ChainStep]
    decrypt_failures: tuple[str, ...] = ()

    @property
    def usable(self) -> bool:
        """有可请求的通道；本地自建服务可以没有 Key"""
        return self.source != "none" and (self.api_key is not None or not self.needs_key)

    @property
    def missing_reason(self) -> str:
        """到 ④ 时最有信息量的那句原因：依次看 ①②③ 谁离命中最近"""
        for step in self.chain:
            if step.reason in (REASON_BAD_KEY, REASON_NO_KEY):
                return step.reason
        for step in self.chain:
            if step.level in (LEVEL_SCENARIO, LEVEL_DEFAULT):
                return step.reason
        return REASON_NOT_CONFIGURED

    def as_dict(self) -> dict:
        return {
            "base_url": self.base_url,
            "source": self.source,
            "chat_model": self.chat_model,
            "work_model": self.work_model,
            "models": self.models,
            "needs_key": self.needs_key,
            "usable": self.usable,
            "missing_reason": self.missing_reason,
            "chain": [s.as_dict() for s in self.chain],
        }


def _credit_source_of(level: str) -> str:
    """记账沿用旧口径：profile / pool_key / user_key —— 额度与并发都按这三个分支走

    场景序列用的是用户自己的配置档，所以与旧的全局默认档同口径（user_key）。
    """
    return {LEVEL_BOUND: "profile", LEVEL_POOL: "pool_key",
            LEVEL_SCENARIO: "user_key", LEVEL_DEFAULT: "user_key"}.get(level, "none")


def _resolution(candidate: Candidate, chain: list[ChainStep],
                decrypt_failures: tuple[str, ...] = ()) -> Resolution:
    if not candidate.hit:
        return Resolution(
            base_url="", api_key=None, chat_model=None, work_model=None,
            source="none", credit_source="none", pool_key_id=None,
            provider_info={}, models=[], thinking_supported=False,
            system_layout="anywhere", needs_key=True, chain=chain,
            decrypt_failures=decrypt_failures,
        )
    info = candidate.provider_info or {}
    base_url = candidate.base_url or ""
    return Resolution(
        base_url=base_url,
        api_key=candidate.api_key,
        chat_model=candidate.chat_model or (info.get("default_model") or None),
        work_model=candidate.work_model or (info.get("work_model") or None),
        source=candidate.level,
        credit_source=_credit_source_of(candidate.level),
        pool_key_id=candidate.pool_key_id,
        provider_info=info,
        models=list(candidate.models) or list(info.get("models") or []),
        thinking_supported=bool(info.get("thinking_supported")),
        system_layout=info.get("system_layout") or "anywhere",
        needs_key=bool(base_url) and not is_local_endpoint(base_url),
        chain=chain,
        decrypt_failures=decrypt_failures,
    )


def resolve(*, bound: Candidate, pool: Candidate, scenario: tuple[Candidate, ...] = (),
            prefer_own: bool = False) -> Resolution:
    """纯判断：按顺序取第一个命中，没命中就是 ④ 失败

    两套顺序，由「优先使用本人配置」决定（用户级或 AI 级，见 _prefer_own）：
    - 关（默认）= **按额度**：① 池 → ② AI 绑定 → ③ 场景序列。
      池那一步自带额度判断（credit > 0），清零时自然跳过，不用每次先撞一次 402；
    - 开 = **优先本人配置**：① AI 绑定 → ③ 场景序列 → ② 池兜底。

    ③ 是一**组**候选而不是一条：场景序列按 position 排好传进来，这里逐格尝试，
    第一个命中即止。整组的跳过原因（scenario_off / no_route）由取数壳放在第一格，
    所以没配序列时链上仍能看出「这一格为什么是空的」。

    顺序由用户 2026-11-25 拍定；2026-11-28 把 ③ 从「一条全局默认档」改成「场景序列」，
    链的档位数量与含义不变，只是 ③ 内部变成有序多格（docs/dev/api_config_chain.md §3.4）。
    """
    chain: list[ChainStep] = []
    groups = ((bound,), tuple(scenario), (pool,)) if prefer_own else ((pool,), (bound,), tuple(scenario))
    failures = tuple(
        f for group in ((bound,), (pool,), tuple(scenario)) for c in group
        for f in c.decrypt_failures
    )
    for group in groups:
        for candidate in group:
            chain.append(ChainStep(
                level=candidate.level,
                status=STATUS_HIT if candidate.hit else STATUS_SKIP,
                reason=candidate.reason,
                label=candidate.label,
                base_url=candidate.base_url,
                model=candidate.chat_model or "",
            ))
            if candidate.hit:
                return _resolution(candidate, chain, failures)
    chain.append(ChainStep(level=LEVEL_FAIL, status=STATUS_SKIP,
                           reason=REASON_NOT_CONFIGURED))
    return _resolution(Candidate(LEVEL_FAIL, REASON_NOT_CONFIGURED), chain, failures)
