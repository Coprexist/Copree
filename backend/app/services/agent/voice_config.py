"""AI 的语音配置读写 + 能力判定（发给谁、能不能用，只在这里回答）

四件事分开：
- 查：voice_of（**行 + 它此刻生效的配置**一次取齐，别处不要再各查一遍）
- 存：save_voice_config（key 加密落库，任何字段一改就把旧的检测结论作废）
- 测：probe_now（测解析后的那一套，把结论写回这张行）
- 判：has_voice_capability（"这个 AI 现在能不能发语音"的唯一口径，工具门控吃到它）

为什么"行"和"生效配置"要一起给：选了语音配置档时，行上的 base_url / voice_id
本来就是空的（真正的值在档里），调用方几乎总是同时要这两样——分开取会退化成
"先 get 再 resolve"在每个调用点各抄一遍，还多查一次库。
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.agent_voice import AgentVoiceConfig
from app.services.agent.api_config_resolver import ResolvedVoice, resolve_voice_config
from app.services.agent.voice_probe import ProbeResult, probe_voice
from app.utils.crypto import decrypt_secret, encrypt_secret

logger = logging.getLogger(__name__)


async def get_voice_config(db: AsyncSession, agent_id: int) -> AgentVoiceConfig | None:
    res = await db.execute(
        select(AgentVoiceConfig).where(AgentVoiceConfig.agent_id == int(agent_id)).limit(1)
    )
    return res.scalars().first()


def api_key_of(cfg: AgentVoiceConfig | None) -> str:
    """这一行手填的明文 key（存盘时比对用）：只在内存里，别写日志、别回给前端"""
    if cfg is None or not cfg.api_key_encrypted:
        return ""
    try:
        return decrypt_secret(cfg.api_key_encrypted)
    except Exception as e:  # noqa: BLE001 —— 换了加密密钥的老行不该让整个能力消失
        logger.warning(f"语音配置解密失败（agent={cfg.agent_id}）: {e}")
        return ""


async def voice_of(
    db: AsyncSession, agent_id: int,
) -> tuple[AgentVoiceConfig | None, ResolvedVoice | None]:
    """一次取齐：这行 + 它此刻实际生效的配置（没有行 = (None, None)）

    生效配置的两种来源只在解析层判断：
    - 选了语音配置档 → 整包来自那条档（厂商地址 + 档里的 Key + 音色），行上的字段不看；
    - 没选 → 用这行手填的厂商/地址/Key/音色（老数据照旧跑）。
    """
    row = (await db.execute(
        select(AgentVoiceConfig, Agent.owner_id)
        .join(Agent, Agent.id == AgentVoiceConfig.agent_id)
        .where(AgentVoiceConfig.agent_id == int(agent_id))
        .limit(1)
    )).first()
    if row is None:
        return None, None
    cfg, owner_id = row
    return cfg, await resolve_voice_config(db, cfg, owner_user_id=owner_id)


async def _resolve_of(db: AsyncSession, cfg: AgentVoiceConfig) -> ResolvedVoice:
    """按行解析一次（owner 不在行上，现场查）：probe 拿到的是行、不是刚查的那对"""
    owner_id = (await db.execute(
        select(Agent.owner_id).where(Agent.id == int(cfg.agent_id))
    )).scalar()
    return await resolve_voice_config(db, cfg, owner_user_id=owner_id)


async def has_voice_capability(db: AsyncSession, agent_id: int) -> bool:
    """这个 AI 现在能不能发语音：**配了且检测通过**才算

    工具门控只认这一处，免得"配置存在"和"能不能用"在两个地方各判一遍。
    地址/音色取解析后的值——选了配置档时，行上的字段本来就可能是空的。
    """
    cfg, eff = await voice_of(db, agent_id)
    return bool(cfg is not None and cfg.probe_ok and eff and eff.base_url and eff.voice_id)


async def save_voice_config(
    db: AsyncSession, agent_id: int, *,
    provider: str, base_url: str, api_key: str | None, voice_id: str, model: str = "",
) -> AgentVoiceConfig:
    """写一套语音配置。任何字段变了 → 旧检测结论立刻作废（宁可多测一次，不可误用）。"""
    cfg = await get_voice_config(db, agent_id)
    if cfg is None:
        cfg = AgentVoiceConfig(agent_id=int(agent_id))
        db.add(cfg)
    changed = (
        (cfg.provider or "") != (provider or "")
        or (cfg.base_url or "") != (base_url or "")
        or (cfg.voice_id or "") != (voice_id or "")
        or (cfg.model or "") != (model or "")
        or (api_key is not None and (api_key or "") != api_key_of(cfg))
    )
    cfg.provider = provider or ""
    cfg.base_url = base_url or ""
    cfg.voice_id = voice_id or ""
    cfg.model = model or ""
    if api_key is not None:
        cfg.api_key_encrypted = encrypt_secret(api_key) if api_key else None
    if changed:
        cfg.probe_ok = False
        cfg.probe_kind = ""
        cfg.probe_message = ""
        cfg.probed_at = None
    await db.flush()
    return cfg


async def probe_now(
    db: AsyncSession, cfg: AgentVoiceConfig, *, allow_private: bool = False,
) -> ProbeResult:
    """测一次并把结论写回（不 commit：交给调用方）

    测的是**解析后**的那一套：选了配置档就测档里的地址/Key/音色，
    否则测行上填的。否则会出现"档里填的是好的、测的却是空行"这种假失败。
    """
    from datetime import datetime, timezone

    eff = await _resolve_of(db, cfg)
    result = await probe_voice(
        eff.provider, eff.base_url, eff.api_key or "",
        voice_id=eff.voice_id, model=eff.model or None,
        allow_private=allow_private,
    )
    cfg.probe_ok = bool(result.ok)
    cfg.probe_kind = result.kind
    cfg.probe_message = result.message[:500] if result.message else ""
    cfg.probed_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.flush()
    return result
