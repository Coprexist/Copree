"""消息撤回 — 站内唯一入口（群聊与私信共用同一套语义）

撤回要同时做到四件事，少一件都会留下"AI 记得你说过、其实你已经撤了"这种坑：

1) 落库标记：原文留在库里（审计/排障），但**任何渲染都不再显示**（见 utils/pure/history.revoked_text）；
2) 给**已经看过它的 AI** 补一条撤回通知——账本段内只追加、不改中段，所以语义是"再补一条作废通知"；
3) 通道侧联动（QQ 只在 2 分钟内、且只能撤机器人自己发的）：由出口注册表负责，这里只交事实；
4) 广播给在线客户端：由调用方（路由 / AI 工具）负责，本模块不碰 WS。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from app.utils.pure.history import revoked_notice

logger = logging.getLogger(__name__)

# 站内撤回窗口：与 QQ 一致
REVOKE_WINDOW_SECONDS = 120


class RevokeDenied(PermissionError):
    """不能撤：不是自己发的（且不是管理员），或者已经撤过了"""


class RevokeExpired(PermissionError):
    """超过撤回窗口"""


def _now() -> datetime:
    # 与库里的 created_at 同口径（naive UTC）
    return datetime.now(timezone.utc).replace(tzinfo=None)


def check_revocable(message, *, actor_id: int | None, is_admin: bool = False) -> None:
    """能不能撤：没撤过、是自己的（管理员可撤别人的）、在窗口内。"""
    if getattr(message, "revoked_at", None):
        raise RevokeDenied("这条已经撤回了")
    if not is_admin and int(getattr(message, "sender_id", 0) or 0) != int(actor_id or 0):
        raise RevokeDenied("只能撤回自己发的消息")
    created = getattr(message, "created_at", None) or _now()
    if (_now() - created).total_seconds() > REVOKE_WINDOW_SECONDS:
        raise RevokeExpired(f"超过 {REVOKE_WINDOW_SECONDS // 60} 分钟，撤不回了")


async def _speaker_name(db, sender_id: int) -> str:
    from app.models.user import User

    user = await db.get(User, sender_id)
    return str(getattr(user, "username", "") or "")


async def _append_revoke_notices(db, *, context_ref: str, message_id: int, speaker: str) -> list[int]:
    """给"账本里已经有这条"的 AI 各补一条撤回通知，返回被通知的 agent id。

    判据是账本里有没有指向这条消息的条目（ref = str(msg_id)）：没看过的 AI 不用补——
    它以后拿到的本来就是占位（渲染层按 revoked_at 走）。
    """
    from sqlalchemy import select

    from app.models.agent import Agent, AgentHistoryEntry
    from app.services.history import context_sync

    agent_ids = (await db.execute(
        select(AgentHistoryEntry.agent_id).where(
            AgentHistoryEntry.context_ref == context_ref,
            AgentHistoryEntry.ref == str(message_id),
        )
    )).scalars().all()
    notified: list[int] = []
    for agent_id in sorted({int(a) for a in agent_ids}):
        agent = await db.get(Agent, agent_id)
        if agent is None:
            continue
        await context_sync.append_events(db, agent, context_ref, [revoked_notice(speaker, message_id)])
        notified.append(agent_id)
    return notified


async def revoke_group_message(db, message, *, actor_id: int | None, is_admin: bool = False) -> dict:
    """撤回一条群消息：标记 + 给看过它的 AI 补通知（广播/通道联动由调用方做）。"""
    from app.services.history.context_sync import context_ref

    check_revocable(message, actor_id=actor_id, is_admin=is_admin)
    speaker = await _speaker_name(db, message.sender_id)
    message.revoked_at = _now()
    message.revoked_by = int(actor_id) if actor_id else None
    await db.flush()
    notified = await _append_revoke_notices(
        db, context_ref=context_ref(group_id=message.group_id), message_id=message.id, speaker=speaker
    )
    # 通道侧一起撤（QQ 只在 2 分钟内、且要它有权限）：结果如实带回给调用方
    from app.chat.outbound import dispatch_revoke

    channel = await dispatch_revoke(db, message.group_id, message)
    logger.info(
        "群 %s 的消息 %s 已撤回（补通知 %s 个 AI，通道 %s）",
        message.group_id, message.id, len(notified), channel or "无",
    )
    return {"revoked": True, "speaker": speaker, "notified_agents": notified, "channel": channel}


async def revoke_dm_message(db, message, *, actor_id: int | None, is_admin: bool = False) -> dict:
    """撤回一条私信：与群消息同一套（标记 + 补通知）。"""
    from app.services.history.context_sync import context_ref

    check_revocable(message, actor_id=actor_id, is_admin=is_admin)
    speaker = await _speaker_name(db, message.sender_id)
    message.revoked_at = _now()
    message.revoked_by = int(actor_id) if actor_id else None
    await db.flush()
    notified = await _append_revoke_notices(
        db, context_ref=context_ref(session_id=str(message.session_id)),
        message_id=message.id, speaker=speaker,
    )
    logger.info("私信 %s 的消息 %s 已撤回（补通知 %s 个 AI）", message.session_id, message.id, len(notified))
    return {"revoked": True, "speaker": speaker, "notified_agents": notified}
