"""API 凭证解析 —— 唯一的回退链入口（LLM 与语音共用一套判断）

为什么要收成一个口子：
原来「用哪把 Key、哪个地址、哪个模型」散在 agent_service / llm_credentials /
model_catalog / 各路由里，每加一层配置就要改 N 处，改漏一处就是线上拿着
DeepSeek 的 Key 去敲通义的地址。这里把两层数据（配置档 / 厂商）和旧的三档
（本 AI、用户全局、代码兜底）收进同一段判断。

纯粹的那一半是 resolve_from()：只吃对象，不查库、不落库，单测直接喂构造的
agent/user/profile/provider。查库只是取数的壳。

回退链（第 1 条命中即返回）：

    配置档（整包：地址 + Key + 模型都来自这一条）
      ↓ 没有选配置档
    本 AI 单独填的地址/Key
      ↓ 没有
    用户全局
      ↓ 没有
    settings 里的兜底

**Key 是唯一不许跨厂商借的一项**，模型同理：地址一旦指向另一家，就不能再拿
上一层的 Key 和模型名去请求——那只会得到一句供应商的 401 或「模型不存在」，
比报「这家还没填 Key」难查得多。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from app.utils.pure.llm_endpoint import thinking_supported_for

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResolvedLlm:
    """一次 LLM 调用该用的地址、Key 与模型"""

    base_url: str
    api_key: str | None
    chat_model: str | None
    work_model: str | None
    # profile | agent | global | fallback —— 出事时能一眼看出是哪一档给的值
    source: str


@dataclass(frozen=True)
class ResolvedVoice:
    """一次语音合成该用的地址、Key、音色"""

    provider: str
    base_url: str
    api_key: str | None
    voice_id: str
    model: str
    source: str


def host_of(url: str | None) -> str:
    """只取主机名：判断"是不是同一家"够用，也不会被端口/路径干扰"""
    if not url:
        return ""
    rest = url.split("://", 1)[-1]
    return rest.split("/", 1)[0].split(":", 1)[0].lower()


def same_provider(a: str | None, b: str | None) -> bool:
    """两个地址是不是同一家厂商（同一家才允许 Key/模型互相继承）"""
    ha, hb = host_of(a), host_of(b)
    return bool(ha) and ha == hb


def _decrypt(enc: str | None) -> str | None:
    if not enc:
        return None
    try:
        from app.utils.crypto import decrypt_api_key
        return decrypt_api_key(enc)
    except Exception as e:  # 密钥轮换/损坏：降级成"没有 Key"，让上层报一句人话
        logger.warning(f"API Key 解密失败，按未配置处理: {e}")
        return None


def resolve_from(
    *,
    agent=None,
    user=None,
    profile=None,
    provider=None,
    default_base_url: str = "",
) -> ResolvedLlm:
    """纯判断：给定这几行数据，用哪一套。

    profile 与 provider 要么都给，要么都不给（调用方负责成对取）；
    profile 存在就整包生效，不往上层退——配置档是"整套配置"，半套没有意义。
    """
    # ── ① 配置档：整包 ──
    if profile is not None and provider is not None:
        return ResolvedLlm(
            base_url=provider.base_url or default_base_url,
            api_key=_decrypt(profile.api_key_encrypted)
            or _decrypt(provider.default_api_key_encrypted),
            # 留空 = 用厂商默认；厂商也没填才是 None（调用方再用模型目录兜底）
            chat_model=profile.primary_model or provider.default_primary,
            work_model=profile.secondary_model or provider.default_secondary,
            source="profile",
        )

    user_base = getattr(user, "api_base_url", None)
    user_key_enc = getattr(user, "api_key_encrypted", None)
    agent_base = getattr(agent, "api_base_url", None)

    # ── ② 本 AI 单独填的 ──
    if agent is not None and (agent_base or getattr(agent, "api_key_encrypted", None)):
        # 地址没填 = 跟随全局那一家，此时 Key/模型可以借；
        # 地址填了就必须是同一家才借，否则宁可没 Key 也不拿别家的去请求。
        can_borrow = same_provider(agent_base, user_base) if agent_base else True
        return ResolvedLlm(
            base_url=agent_base or user_base or default_base_url,
            api_key=_decrypt(getattr(agent, "api_key_encrypted", None))
            or (_decrypt(user_key_enc) if can_borrow else None),
            chat_model=getattr(agent, "chat_model", None)
            or (_global_model(user, "global_chat_model") if can_borrow else None),
            work_model=getattr(agent, "work_model", None)
            or (_global_model(user, "global_work_model") if can_borrow else None),
            source="agent",
        )

    # ── ③ 用户全局 ──
    if user is not None and (user_base or user_key_enc):
        return ResolvedLlm(
            base_url=user_base or default_base_url,
            api_key=_decrypt(user_key_enc),
            chat_model=_global_model(user, "global_chat_model"),
            work_model=_global_model(user, "global_work_model"),
            source="global",
        )

    # ── ④ 兜底 ──
    return ResolvedLlm(default_base_url, None, None, None, "fallback")


def _global_model(user, attr: str) -> str | None:
    value = getattr(user, attr, None) if user is not None else None
    return value or None


async def _load_profile(db, profile_id: int | None, kind: str):
    """取配置档和它的厂商。

    **这里不校验归属**：合作者可以把自己账号下的配置档绑到别人管理的 AI 上
    （写入端 update_agent_config 已校验"操作者本人的档 或 AI 主人的档"）。
    运行时再按主人拦一次，只会让这条已经允许的链路在发消息时静默回落，
    而界面上看着是选上了——比直接报错更难查。
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


def provider_system_layout(provider, base_url: str | None) -> str:
    """这条厂商这一轮该用哪一档 system 摆放（anywhere | leading | none）。

    判定逻辑不在这里：转成与管理员 provider_config 条目相同的形状，交给
    provider_config.resolve_system_layout。**单一入口**是关键——两处各写一份，
    「有没有来源才算证据」这种规则迟早只剩一边还记得。

    以前这里只有 `provider.system_layout or "anywhere"`——而历史行里存着的正是默认的
    anywhere，于是自建的本机 llama-server 每轮都把 system 塞进对话中段，Qwen 的 Jinja
    直接 500（System message must be at the beginning）。
    """
    from app.utils.pure.provider_config import resolve_system_layout

    return resolve_system_layout(
        {
            "system_layout": getattr(provider, "system_layout", None),
            "system_layout_source": getattr(provider, "system_layout_source", None),
        },
        None,
        base_url or getattr(provider, "base_url", None),
    )


def provider_info_of(provider, resolved: ResolvedLlm) -> dict:
    """把「用户的厂商行」转成与 channel_provider_info 同形的通道信息。

    **形状必须一致**：models 是模型 id 的**字符串列表**，不是 [{value,label}]。
    resolve_model 靠 isinstance(m, str) 过滤，形状错了它就静默地不约束模型名，
    结果是把 deepseek-flash 发到通义的地址上，每轮 400 而界面上什么都看不出来。
    """
    return {
        "base_url": resolved.base_url,
        "provider_name": provider.name,
        # 地址即事实：本地自建服务也能开关思考，厂商行里的旧勾选压不掉它
        "thinking_supported": thinking_supported_for(resolved.base_url, bool(provider.thinking_supported)),
        "system_layout": provider_system_layout(provider, resolved.base_url),
        "models": [o["value"] for o in (provider.options or []) if o.get("value")],
        "default_model": resolved.chat_model,
        "work_model": resolved.work_model,
    }


def _owner_of(agent) -> int | None:
    """这个 AI 的主人（真实用户）。

    注意 owner_id 才是主人：agents.user_id 是 AI 自己的虚拟账号，
    拿它去找配置档必然找不到——那会静默退回全局，用错 Key 还查不出来。
    """
    return getattr(agent, "owner_id", None) or getattr(agent, "user_id", None)


async def resolve_llm_config(db, agent) -> ResolvedLlm:
    """查库取数 + 纯判断：给这个 AI 解析一次 LLM 配置"""
    from app.config import settings
    from app.models.user import User

    owner_id = _owner_of(agent)
    user = await db.get(User, owner_id) if owner_id else None
    profile, provider = await _load_profile(
        db, getattr(agent, "llm_profile_id", None), "llm"
    )
    return resolve_from(
        agent=agent,
        user=user,
        profile=profile,
        provider=provider,
        default_base_url=settings.deepseek_base_url,
    )


# ── 语音 ──────────────────────────────────────────────────────────────

def resolve_voice_from(*, voice=None, user=None, profile=None, provider=None) -> ResolvedVoice:
    """语音整条链：配置档 → 这条语音记录自己填的 → 用户全局

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

    owner_user_id 由调用方给（语音行上没有这一列）：只在"没选配置档、地址没填全"时
    用来判断能不能借用户全局那把 Key，传 None 就等于不借。
    """
    from app.models.user import User

    owner_id = owner_user_id or getattr(voice, "owner_user_id", None)
    user = await db.get(User, owner_id) if owner_id else None
    profile, provider = await _load_profile(
        db, getattr(voice, "voice_profile_id", None), "voice"
    )
    return resolve_voice_from(voice=voice, user=user, profile=profile, provider=provider)
