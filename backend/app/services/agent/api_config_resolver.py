"""API 配置解析 —— 全仓唯一入口

三档，每一档**整条命中或整条跳过**（地址、Key、模型一起取，绝不跨档拼接）：

    ① AI 绑定的配置    agents.llm_profile_id → 厂商在、档在、Key 可解
          ↓ 未命中
    ② 池配置          账单人有额度、分得到成员、成员的 kind 与本次请求一致
          ↓ 未命中
    ③ 用户默认配置    user_api_profiles 里该用户 kind=llm 且 is_default 的那条
          ↓ 未命中
    ④ 失败            报「未绑定可用配置」，不再打 settings.deepseek_base_url

为什么收成一个口子：同一件事（用谁的 Key、打哪个地址、用哪个模型）曾有四五份实现，
每份都自称唯一入口；界面上的「生效中」与实际请求也因此可能不是同一条。这里把判断
收成 resolve()（纯函数，只吃候选），查库只是取数的壳。

**没有第二档兜底地址**：旧链最后一级是 settings.deepseek_base_url + 空 Key，而系统级
配置按既定口径不存 Key —— 那一级永远打不通，只是把「没配置」伪装成「配置了」。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy import select

from app.utils.pure.llm_endpoint import is_local_endpoint

logger = logging.getLogger(__name__)

# ── 档位 ──────────────────────────────────────────────────────────────
LEVEL_BOUND = "bound"
LEVEL_POOL = "pool"
LEVEL_DEFAULT = "default"
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
REASON_NO_DEFAULT = "no_default"     # 用户没设默认配置
REASON_EXCLUDED = "excluded"         # 上一次已经用这一档失败过
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
            if step.level == LEVEL_DEFAULT:
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
    """记账沿用旧口径：profile / pool_key / user_key —— 额度与并发都按这三个分支走"""
    return {LEVEL_BOUND: "profile", LEVEL_POOL: "pool_key",
            LEVEL_DEFAULT: "user_key"}.get(level, "none")


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


def resolve(*, bound: Candidate, pool: Candidate, default: Candidate) -> Resolution:
    """纯判断：按 ① → ② → ③ 取第一个命中，没命中就是 ④ 失败

    顺序固定（池在用户默认之前）。旧开关 prefer_own_key 的「打开」就是这个顺序，
    已被用户的意图取代 —— 不再保留一个能改变优先级的开关。
    """
    chain: list[ChainStep] = []
    failures = tuple(f for c in (bound, pool, default) for f in c.decrypt_failures)
    for candidate in (bound, pool, default):
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


# ── 取数辅助 ──────────────────────────────────────────────────────────

def host_of(url: str | None) -> str:
    """只取主机名：判断「是不是同一家」够用，也不会被端口/路径干扰"""
    if not url:
        return ""
    rest = url.split("://", 1)[-1]
    return rest.split("/", 1)[0].split(":", 1)[0].lower()


def same_provider(a: str | None, b: str | None) -> bool:
    """两个地址是不是同一家厂商（同一家才允许 Key/模型互相继承）"""
    ha, hb = host_of(a), host_of(b)
    return bool(ha) and ha == hb


def _decrypt(enc: str | None) -> str | None:
    """兼容旧调用：解不出来就当没有 Key"""
    return _decrypt_checked(enc)[0]


def _decrypt_checked(enc: str | None) -> tuple[str | None, bool]:
    """(明文, 是否没出错)

    区分「没填」与「解不出来」：前者的跳过原因是 no_key，后者是 bad_key ——
    界面要分别告诉用户「去填一条」还是「加密密钥换过了」。
    """
    if not enc:
        return None, True
    try:
        from app.utils.crypto import decrypt_api_key
        return decrypt_api_key(enc), True
    except Exception as e:  # 密钥轮换 / 数据损坏：降级成「没有 Key」，但原因要能说出来
        logger.warning(f"API Key 解密失败，按未配置处理: {e}")
        return None, False


def _owner_of(agent) -> int | None:
    """这个 AI 的主人（真实用户）。

    注意 owner_id 才是主人：agents.user_id 是 AI 自己的虚拟账号，
    拿它去找配置档必然找不到 —— 那会静默退回全局，用错 Key 还查不出来。
    """
    return getattr(agent, "owner_id", None) or getattr(agent, "user_id", None)


async def _load_profile(db, profile_id: int | None, kind: str):
    """取配置档和它的厂商。

    **这里不校验归属**：合作者可以把自己账号下的配置档绑到别人管理的 AI 上
    （写入端 update_agent_config 已校验「操作者本人的档 或 AI 主人的档」）。
    运行时再按主人拦一次，只会让这条已经允许的链路在发消息时静默回落，
    而界面上看着是选上了 —— 比直接报错更难查。
    """
    if not profile_id:
        return None, None
    from app.models.api_profile import UserApiProvider, UserApiProfile

    profile = await db.get(UserApiProfile, profile_id)
    if profile is None:
        return None, None
    provider = await db.get(UserApiProvider, profile.provider_id)
    if provider is None or provider.kind != kind:
        return None, None
    return profile, provider


async def _load_default_profile(db, user_id: int | None, kind: str):
    """该用户在 config.kind 下的默认配置档（写入端保证同 kind 只有一条）"""
    if not user_id:
        return None, None
    from app.models.api_profile import UserApiProvider, UserApiProfile

    stmt = (
        select(UserApiProfile)
        .join(UserApiProvider, UserApiProvider.id == UserApiProfile.provider_id)
        .where(
            UserApiProfile.user_id == user_id,
            UserApiProfile.is_default.is_(True),
            UserApiProvider.kind == kind,
        )
        .order_by(UserApiProfile.sort_order, UserApiProfile.id)
    )
    profile = (await db.execute(stmt)).scalars().first()
    if profile is None:
        return None, None
    provider = await db.get(UserApiProvider, profile.provider_id)
    if provider is None or provider.kind != kind:
        return None, None
    return profile, provider


def _profile_candidate(level: str, profile, provider) -> Candidate:
    """把一条配置档（+ 它的厂商）压成候选，整条取：地址、Key、模型都来自这两行"""
    from app.utils.pure.provider_config import channel_provider_info

    label = getattr(profile, "name", "") or ""
    base_url = provider.base_url or ""
    chat_model = profile.primary_model or provider.default_primary
    work_model = profile.secondary_model or provider.default_secondary
    info = channel_provider_info(
        {
            "name": provider.name,
            "system_layout": provider.system_layout,
            "system_layout_source": provider.system_layout_source,
            "thinking_supported": provider.thinking_supported,
            "model_options": [o for o in (provider.options or []) if isinstance(o, dict)],
        },
        base_url,
        default_model=chat_model,
    )
    # 配置档的 Key 优先，没填才用厂商的默认 Key —— 只在这一家内部回退
    key, ok = _decrypt_checked(profile.api_key_encrypted or provider.default_api_key_encrypted)
    if not ok:
        return Candidate(level, REASON_BAD_KEY, label=label, base_url=base_url,
                         decrypt_failures=(f"{level}:{label or base_url}",))
    if key is None and not is_local_endpoint(base_url):
        return Candidate(level, REASON_NO_KEY, label=label, base_url=base_url)
    return Candidate(
        level, REASON_OK, label=label, base_url=base_url, api_key=key,
        chat_model=chat_model, work_model=work_model, provider_info=info,
        models=tuple(info["models"]),
    )


async def pool_candidate(db, user, *, exclude_pool_key_id: int | None = None) -> Candidate:
    """② 池配置：有额度、分得到成员、成员可用

    这一批仍读旧的 api_key_pool（表结构改造在下一批），但判断已经收在这里，
    调用方不再自己拼「额度 / 池 Key / 地址」。
    """
    if user is None:
        return Candidate(LEVEL_POOL, REASON_NO_QUOTA)
    credit = max(0, user.platform_gifted_credit or 0) + (user.api_credit or 0)
    if credit <= 0:
        return Candidate(LEVEL_POOL, REASON_NO_QUOTA)

    from app.services.infrastructure.quota_service import find_best_pool_key

    pool_key = await find_best_pool_key(db, user.id, exclude_pool_key_id=exclude_pool_key_id)
    if pool_key is None:
        return Candidate(LEVEL_POOL, REASON_NO_MEMBER)

    label = getattr(pool_key, "name", "") or f"池 Key #{pool_key.id}"
    base_url = pool_key.api_base_url or ""
    key, ok = _decrypt_checked(pool_key.api_key_encrypted)
    if not ok:
        return Candidate(LEVEL_POOL, REASON_BAD_KEY, label=label, base_url=base_url,
                         decrypt_failures=(f"pool:{label}",))
    if key is None and not is_local_endpoint(base_url):
        return Candidate(LEVEL_POOL, REASON_NO_KEY, label=label, base_url=base_url)

    from app.services.infrastructure.system_settings_service import get_provider_for_pool_key
    from app.utils.pure.provider_config import channel_provider_info

    pool_provider = await get_provider_for_pool_key(db, pool_key)
    # 池 Key 自己记的模型清单优先（models/default_model）；没记才用它关联的供应商预设
    info = channel_provider_info(
        pool_provider, base_url,
        models=getattr(pool_key, "models", None),
        default_model=getattr(pool_key, "default_model", None),
    )
    return Candidate(
        LEVEL_POOL, REASON_OK, label=label, base_url=base_url, api_key=key,
        chat_model=info.get("default_model"), provider_info=info,
        pool_key_id=pool_key.id, models=tuple(info["models"]),
    )


async def resolve_llm(
    db, agent, *,
    bill_to: int | None = None,
    kind: str = "llm",
    exclude: set[str] | frozenset[str] = frozenset(),
    exclude_pool_key_id: int | None = None,
    force_own_key: bool = False,
) -> Resolution:
    """查库取数 + 纯判断：给这个 AI 解析一次 LLM 配置

    bill_to 由调用方算（群聊谁付费、外部通道记主人头上 —— 那是业务规则，
    不是「用哪条配置」的一部分）。不传就是 AI 的主人。
    """
    from app.models.user import User

    bill_user_id = bill_to or _owner_of(agent)
    user = await db.get(User, bill_user_id) if bill_user_id else None

    # ① AI 绑定的配置
    if LEVEL_BOUND in exclude:
        bound = Candidate(LEVEL_BOUND, REASON_EXCLUDED)
    elif not getattr(agent, "llm_profile_id", None):
        bound = Candidate(LEVEL_BOUND, REASON_NO_BIND)
    else:
        profile, provider = await _load_profile(db, agent.llm_profile_id, kind)
        if profile is None or provider is None:
            bound = Candidate(LEVEL_BOUND, REASON_BAD_BIND)
        else:
            bound = _profile_candidate(LEVEL_BOUND, profile, provider)

    # ② 池配置
    if LEVEL_POOL in exclude:
        pool = Candidate(LEVEL_POOL, REASON_EXCLUDED)
    elif force_own_key:
        pool = Candidate(LEVEL_POOL, REASON_OWN_KEY_ONLY)
    else:
        pool = await pool_candidate(db, user, exclude_pool_key_id=exclude_pool_key_id)

    # ③ 用户默认配置
    if LEVEL_DEFAULT in exclude:
        default = Candidate(LEVEL_DEFAULT, REASON_EXCLUDED)
    elif user is None:
        default = Candidate(LEVEL_DEFAULT, REASON_NO_DEFAULT)
    else:
        profile, provider = await _load_default_profile(db, user.id, kind)
        if profile is None or provider is None:
            default = Candidate(LEVEL_DEFAULT, REASON_NO_DEFAULT, label=user.username or "")
        else:
            default = _profile_candidate(LEVEL_DEFAULT, profile, provider)

    resolution = resolve(bound=bound, pool=pool, default=default)
    if not resolution.usable:
        logger.info(
            "AI %s(%s) 没有可用 API 配置：%s",
            getattr(agent, "name", "?"), getattr(agent, "id", "?"),
            " → ".join(f"{s.level}:{s.reason}" for s in resolution.chain),
        )
    return resolution


async def resolve_user_llm(
    db, user_id: int | None, *, kind: str = "llm",
    exclude: set[str] | frozenset[str] = frozenset(),
    exclude_pool_key_id: int | None = None,
) -> Resolution:
    """给「某个人」解析一次（世界 AI / 创建助手 / 管理端探测）：没有 ① 绑定档。

    链变成 ② 池 → ③ 用户默认 —— 与 resolve_llm 是同一个纯函数、同一套原因词表，
    只是 ① 恒为 no_bind。这几处以前各写一份「用户 Key → 池 Key → 全局默认」，
    与 AI 侧的链迟早分叉，也正是「用错 Key 敲错地址」的来源之一。
    """
    from app.models.user import User

    user = await db.get(User, user_id) if user_id else None
    bound = Candidate(LEVEL_BOUND, REASON_NO_BIND)
    if LEVEL_POOL in exclude:
        pool = Candidate(LEVEL_POOL, REASON_EXCLUDED)
    else:
        pool = await pool_candidate(db, user, exclude_pool_key_id=exclude_pool_key_id)
    if LEVEL_DEFAULT in exclude:
        default = Candidate(LEVEL_DEFAULT, REASON_EXCLUDED)
    elif user is None:
        default = Candidate(LEVEL_DEFAULT, REASON_NO_DEFAULT)
    else:
        profile, provider = await _load_default_profile(db, user.id, kind)
        if profile is None or provider is None:
            default = Candidate(LEVEL_DEFAULT, REASON_NO_DEFAULT)
        else:
            default = _profile_candidate(LEVEL_DEFAULT, profile, provider)
    return resolve(bound=bound, pool=pool, default=default)


# ── 语音（结构与 LLM 同形，链路后续照抄；这里先保持原样） ─────────────

@dataclass(frozen=True)
class ResolvedVoice:
    """一次语音合成该用的地址、Key、音色"""

    provider: str
    base_url: str
    api_key: str | None
    voice_id: str
    model: str
    source: str


def resolve_voice_from(*, voice=None, user=None, profile=None, provider=None) -> ResolvedVoice:
    """语音整条链：配置档 → 这条语音记录自己填的

    语音的 Key 与地址同样不许跨厂商借；音色是各家自己的 id 体系，跨家继承没有意义。
    """
    if profile is not None and provider is not None:
        return ResolvedVoice(
            provider=provider.preset_key or "",
            base_url=provider.base_url or "",
            api_key=_decrypt(profile.api_key_encrypted)
            or _decrypt(provider.default_api_key_encrypted),
            voice_id=profile.primary_model or provider.default_primary or "",
            model=provider.default_secondary or "",
            source="profile",
        )

    if voice is not None and (getattr(voice, "base_url", None)
                              or getattr(voice, "api_key_encrypted", None)):
        can_borrow = (same_provider(voice.base_url, getattr(user, "api_base_url", None))
                      if voice.base_url else True)
        return ResolvedVoice(
            provider=getattr(voice, "provider", "") or "",
            base_url=voice.base_url or "",
            api_key=_decrypt(getattr(voice, "api_key_encrypted", None))
            or (_decrypt(getattr(user, "api_key_encrypted", None)) if can_borrow else None),
            voice_id=getattr(voice, "voice_id", "") or "",
            model=getattr(voice, "model", "") or "",
            source="voice",
        )

    return ResolvedVoice(provider="", base_url="", api_key=None, voice_id="", model="",
                         source="none")


async def resolve_voice_config(db, voice, *, owner_user_id=None) -> ResolvedVoice:
    """查库取数 + 纯判断：给这条语音配置解析一次

    owner_user_id 由调用方给（语音行上没有这一列）：只在「没选配置档、地址没填全」时
    用来判断能不能借用户全局那把 Key，传 None 就等于不借。
    """
    from app.models.user import User

    owner_id = owner_user_id or getattr(voice, "owner_user_id", None)
    user = await db.get(User, owner_id) if owner_id else None
    profile, provider = await _load_profile(
        db, getattr(voice, "voice_profile_id", None), "voice"
    )
    return resolve_voice_from(voice=voice, user=user, profile=profile, provider=provider)