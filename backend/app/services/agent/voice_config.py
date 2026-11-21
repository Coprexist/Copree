"""AI 的语音配置读写 + 能力判定（发给谁、能不能用，只在这里回答）

三件事分开：
- 存：save_voice_config（key 加密落库，任何字段一改就把旧的检测结论作废）
- 测：probe_now（调 voice_probe，把结论写回这张行——下一轮请求不必再打供应商）
- 判：has_voice_capability（"这个 AI 现在能不能发语音"的唯一口径，工具门控吃到它）
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_voice import AgentVoiceConfig
from app.services.agent.voice_probe import ProbeResult, probe_voice
from app.utils.crypto import decrypt_secret, encrypt_secret

logger = logging.getLogger(__name__)


async def get_voice_config(db: AsyncSession, agent_id: int) -> AgentVoiceConfig | None:
    res = await db.execute(
        select(AgentVoiceConfig).where(AgentVoiceConfig.agent_id == int(agent_id)).limit(1)
    )
    return res.scalars().first()


def api_key_of(cfg: AgentVoiceConfig | None) -> str:
    """解密出来的明文 key：只在内存里用，别写日志、别回给前端"""
    if cfg is None or not cfg.api_key_encrypted:
        return ""
    try:
        return decrypt_secret(cfg.api_key_encrypted)
    except Exception as e:  # noqa: BLE001 —— 换了加密密钥的老行不该让整个能力消失
        logger.warning(f"语音配置解密失败（agent={cfg.agent_id}）: {e}")
        return ""


async def has_voice_capability(db: AsyncSession, agent_id: int) -> bool:
    """这个 AI 现在能不能发语音：**配了且检测通过**才算

    工具门控只认这一处，免得"配置存在"和"能不能用"在两个地方各判一遍。
    """
    cfg = await get_voice_config(db, agent_id)
    return bool(cfg is not None and cfg.probe_ok and cfg.base_url and cfg.voice_id)


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
    """测一次并把结论写回（不 commit：交给调用方）"""
    from datetime import datetime, timezone

    result = await probe_voice(
        cfg.provider, cfg.base_url, api_key_of(cfg),
        voice_id=cfg.voice_id, model=cfg.model or None,
        allow_private=allow_private,
    )
    cfg.probe_ok = bool(result.ok)
    cfg.probe_kind = result.kind
    cfg.probe_message = result.message[:500] if result.message else ""
    cfg.probed_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.flush()
    return result
