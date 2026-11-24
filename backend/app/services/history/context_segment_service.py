"""上下文压缩段的存取（对 ContextSegment 的唯一读写口）。

两个用途：
1. 压缩发生时把**折叠前的 messages** 存下来（executor 的两个压缩点调用 save）；
2. AI 调 expand_context 时按段号取回（load）。

为什么要有 load 的归属校验：段号很短（8 位十六进制），不校验就等于给了一个可枚举的
接口，能读到别的 AI 的对话。所有查询都带 agent_id。
"""
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.context_segment import ContextSegment

logger = logging.getLogger(__name__)

# 折叠原文留多久：够 AI 在接下来的几轮里回看就够了，再久就只是白占空间
RETENTION_DAYS = 7


def new_segment_id() -> str:
    """8 位十六进制。碰撞概率 1/2^32，主键冲突会直接失败而不是静默覆盖。"""
    return secrets.token_hex(4)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def save(
    db: AsyncSession, *,
    segment_id: str,
    agent_id: int,
    context_ref: str,
    messages: list[dict],
    conversation_type: str = "group",
    group_id: int | None = None,
    session_id: str | None = None,
) -> str:
    """存一段被折叠的原文，返回段号（调用方已经把它写进提示文案了）。"""
    from app.utils.multimodal import strip_image_bytes

    payload = strip_image_bytes(messages or [])
    try:
        from app.services.memory.context_compression_service import estimate_tokens
        token_count = int(estimate_tokens(payload))
    except Exception:  # noqa: BLE001 —— 估算失败不影响存段（只影响提示里的数字）
        token_count = 0

    row = ContextSegment(
        id=segment_id,
        agent_id=agent_id,
        context_ref=context_ref or "",
        conversation_type=conversation_type or "group",
        group_id=group_id,
        session_id=session_id,
        message_count=len(payload),
        token_count=token_count,
        payload=payload,
        expires_at=_now() + timedelta(days=RETENTION_DAYS),
    )
    db.add(row)
    await db.flush()
    logger.info(
        "上下文压缩段已存: agent=%s ref=%s 段=%s 条数=%s tokens=%s",
        agent_id, context_ref, segment_id, row.message_count, token_count,
    )
    return segment_id


async def load(db: AsyncSession, *, agent_id: int, segment_id: str) -> dict | None:
    """按段号取回原文；只认自己的段。"""
    result = await db.execute(
        select(ContextSegment).where(
            ContextSegment.id == str(segment_id),
            ContextSegment.agent_id == agent_id,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        return None
    return {
        "id": row.id,
        "context_ref": row.context_ref,
        "message_count": row.message_count,
        "token_count": row.token_count,
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "messages": row.payload or [],
    }


async def purge_expired(db: AsyncSession) -> int:
    """清掉过期段。启动时跑一次即可——段是低频数据，不需要定时任务。"""
    result = await db.execute(
        delete(ContextSegment).where(ContextSegment.expires_at < _now())
    )
    try:
        await db.commit()
    except Exception:  # noqa: BLE001
        await db.rollback()
    return int(result.rowcount or 0)
