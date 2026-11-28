"""API 配置解析 —— 全仓唯一入口（取数壳）

三档，每一档**整条命中或整条跳过**（地址、Key、模型一起取，绝不跨档拼接）：

    ① 池配置           账单人有额度、分得到成员、成员的 kind 与本次请求一致
          ↓ 未命中
    ② AI 绑定的配置     agents.llm_profile_id → 厂商在、档在、Key 可解
          ↓ 未命中
    ③ 该场景的默认配置  场景序列（user_api_scenario_routes）按 position 逐格尝试
          ↓ 未命中
    ④ 失败              报「未绑定可用配置」，不再打 settings.deepseek_base_url

纯判断在 resolver_core.py（无数据库依赖）；本文件只负责查库、解密、把候选凑齐。
两边的分工是「规则 vs 取数」——规则改在 core，数据来源改在这里。

**没有第二档兜底地址**：旧链最后一级是 settings.deepseek_base_url + 空 Key，
而系统级配置按既定口径不存 Key —— 那一级永远打不通，只是把「没配置」伪装成「配置了」。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select

from app.services.agent.resolver_core import (
    LEVEL_BOUND, LEVEL_DEFAULT, LEVEL_FAIL, LEVEL_POOL, LEVEL_SCENARIO,
    REASON_BAD_BIND, REASON_BAD_KEY, REASON_EXCLUDED, REASON_NO_BIND,
    REASON_NO_DEFAULT, REASON_NO_KEY, REASON_NO_MEMBER, REASON_NO_QUOTA,
    REASON_NO_ROUTE, REASON_NOT_CONFIGURED, REASON_OK, REASON_OWN_KEY_ONLY,
    REASON_SCENARIO_OFF, STATUS_HIT, STATUS_SKIP,
    Candidate, ChainStep, Resolution, resolve,
)
from app.utils.pure.llm_endpoint import is_local_endpoint

logger = logging.getLogger(__name__)

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


async def scenario_enabled(db, user_id: int | None, scenario: str) -> bool:
    """这个场景允不允许用默认配置兜底

    没有记录 = 开（见 utils/pure/api_scenarios.py）：用户不用先「开启」，
    只有主动关掉某个场景时库里才有一行。未知 key 按开处理并记一条 warning——
    拼错场景名不该静默把用户的默认配置断掉，但要在日志里露出来。
    """
    from app.models.api_profile import UserApiScenarioOff
    from app.utils.pure.api_scenarios import get_scenario

    if get_scenario(scenario) is None:
        logger.warning("未知的默认配置场景 %s，按开启处理（检查调用方拼写）", scenario)
        return True
    if not user_id:
        return True
    row = (await db.execute(
        select(UserApiScenarioOff.id).where(
            UserApiScenarioOff.user_id == user_id,
            UserApiScenarioOff.scenario == scenario,
        ).limit(1)
    )).first()
    return row is None


async def _scenario_route_rows(db, user_id: int | None, scenario: str, kind: str):
    """场景序列中启用着的格：[(route, profile, provider)]，按 position 升序

    只读路由本身，不判总开关——总开关是调用方的事（要给出不同的跳过原因）。
    """
    from app.models.api_profile import (
        UserApiProfile, UserApiProvider, UserApiScenarioRoute,
    )

    if not user_id or not scenario:
        return []
    rows = (await db.execute(
        select(UserApiScenarioRoute, UserApiProfile, UserApiProvider)
        .join(UserApiProfile, UserApiProfile.id == UserApiScenarioRoute.profile_id)
        .join(UserApiProvider, UserApiProvider.id == UserApiProfile.provider_id)
        .where(
            UserApiScenarioRoute.user_id == user_id,
            UserApiScenarioRoute.scenario == scenario,
            UserApiScenarioRoute.enabled.is_(True),
            UserApiProvider.kind == kind,
        )
        .order_by(UserApiScenarioRoute.position, UserApiScenarioRoute.id)
    )).all()
    return list(rows)


async def _scenario_candidates(db, user_id: int | None, scenario: str | None,
                               kind: str) -> tuple[Candidate, ...]:
    """第 ③ 格的候选组：正常时多条有序候选，空时一条带原因的占位

    占位刻意放进链里：没有它，界面只能看到「③ 没过」，分不清是「没配序列」
    还是「这个场景被整体关掉了」。
    """
    if not scenario:
        return ()
    if not await scenario_enabled(db, user_id, scenario):
        return (Candidate(LEVEL_SCENARIO, REASON_SCENARIO_OFF),)
    rows = await _scenario_route_rows(db, user_id, scenario, kind)
    if not rows:
        return (Candidate(LEVEL_SCENARIO, REASON_NO_ROUTE),)
    return tuple(_profile_candidate(LEVEL_SCENARIO, profile, provider)
                 for _, profile, provider in rows)


def _prefer_own(agent, user) -> bool:
    """两级开关：AI 级（三态）覆盖用户级

    None = 跟随用户级；True / False = 这个 AI 自己定。同一账号下「聊天 AI 用池额度、
    整理 AI 用自己的 Key」是合理的两种选择，所以 AI 级必须能覆盖用户级，不能只是叠加。
    """
    own = getattr(agent, "prefer_own_config", None)
    if own is not None:
        return bool(own)
    return bool(getattr(user, "prefer_own_key", False))

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
    scenario: str | None = None,
) -> Resolution:
    """查库取数 + 纯判断：给这个 AI 解析一次 LLM 配置

    scenario 是这次请求属于哪个业务场景（见 utils/pure/api_scenarios.py）：
    只门控 ③ 用户默认档——① AI 绑定的档是用户对这个 AI 的显式指定，不该被场景开关影响；
    不传 = 不做场景门控（探测、自检这类「只是问一下」的调用）。

    bill_to 由调用方算（群聊谁付费、外部通道记主人头上 —— 那是业务规则，
    不是「用哪条配置」的一部分）。不传就是 AI 的主人。
    """
    from app.models.user import User

    bill_user_id = bill_to or _owner_of(agent)
    user = await db.get(User, bill_user_id) if bill_user_id else None
    # 「优先使用本人配置」：AI 级三态覆盖用户级（见 _prefer_own）
    prefer_own = _prefer_own(agent, user)

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

    # ③ 该场景的默认配置序列（没传场景时这一格为空，链上不出现）
    if LEVEL_SCENARIO in exclude:
        routes = (Candidate(LEVEL_SCENARIO, REASON_EXCLUDED),)
    else:
        routes = await _scenario_candidates(db, user.id if user else None, scenario, kind)

    resolution = resolve(bound=bound, pool=pool, scenario=routes, prefer_own=prefer_own)
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
    scenario: str | None = None,
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
    if LEVEL_SCENARIO in exclude:
        routes = (Candidate(LEVEL_SCENARIO, REASON_EXCLUDED),)
    else:
        routes = await _scenario_candidates(db, user.id if user else None, scenario, kind)
    return resolve(bound=bound, pool=pool, scenario=routes)


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


async def resolve_voice_config(
    db, voice, *, owner_user_id=None, scenario: str | None = None
) -> ResolvedVoice:
    """查库取数 + 纯判断：给这条语音配置解析一次

    owner_user_id 由调用方给（语音行上没有这一列）：只在「没选配置档、地址没填全」时
    用来判断能不能借用户全局那把 Key，传 None 就等于不借。

    兜底顺序：这条语音绑的档 → 这条语音自己填的 → 用户默认语音档（scenario 开着才用）。
    默认档排在最后，是为了不顶掉已经在用的显式配置——它只补「什么都没填」的那种行。
    """
    from app.models.user import User

    owner_id = owner_user_id or getattr(voice, "owner_user_id", None)
    user = await db.get(User, owner_id) if owner_id else None
    profile, provider = await _load_profile(
        db, getattr(voice, "voice_profile_id", None), "voice"
    )
    # 兜底顺序：这条语音绑的档 → 它自己填的 → 场景序列第一格
    # 场景排在最后，避免顶掉已经在用的显式配置；总开关关掉时整格跳过。
    if profile is None:
        has_own = bool(getattr(voice, "base_url", None)
                       or getattr(voice, "api_key_encrypted", None))
        if not has_own and scenario is not None and user is not None:
            if await scenario_enabled(db, user.id, scenario):
                rows = await _scenario_route_rows(db, user.id, scenario, "voice")
                if rows:
                    _, profile, provider = rows[0]
    return resolve_voice_from(voice=voice, user=user, profile=profile, provider=provider)