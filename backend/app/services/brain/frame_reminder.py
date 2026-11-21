"""同一段上下文被反复唤醒时的劝告（投递制：一段上下文只劝一次）。

口径见 docs/dev/decision_proxy_llm.md §7：同一段上下文内被唤醒超过阈值，就往这一轮里递一句
建议——用现成的 `set_dnd` / `mute_group` / `silence_member` / `update_ruler_time` /
决策代理的 `silent` 规则给自己设界限。平台只提醒，不替它设。

**投递记录在账本里，不记在帧上**：群帧一轮一换（聊完就 end 掉，下一轮是新帧实例），
帧上的 `delivered` 撑不过两轮，拿它做幂等会让同一句话每轮都再投一次（2026-11-02 实测：
agent 57 在群 83 连续六轮各投一次）。账本条目带 `drop_on_unlock`，解锁时投递过才离场，
语义正好是"这段上下文知道过就够，下段上下文再劝"。

计数不另建：今天这个群把本尊叫起来几次，数调用日志 `origin='call'` 即可（单一来源）。
"""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.config import settings

logger = logging.getLogger(__name__)

# 账本条目的 ref：一天一条，靠它判"这段上下文今天劝过没有"
REMIND_REF_PREFIX = "remind:over_woken:"


def _today_suffix() -> str:
    """按展示时区算日期：提醒文案里说的"今天"必须与 AI 看到的一致"""
    tz = timezone(timedelta(hours=8))
    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(settings.display_timezone or "Asia/Shanghai")
    except Exception:  # noqa: BLE001 —— 时区库拿不到就按东八区算，提醒不是关键路径
        pass
    return datetime.now(tz).strftime("%Y-%m-%d")


async def _wakes_today(db, agent_id: int, group_id: int) -> int:
    """今天这个群把本尊叫起来几次：以调用日志为准。

    `origin='call'` 才是真起了一轮；`switch` 是换帧那一笔，不算唤醒。
    """
    from app.models.conversation_log import ConversationLog

    try:
        return (await db.execute(
            select(func.count(ConversationLog.id)).where(
                ConversationLog.agent_id == agent_id,
                ConversationLog.group_id == group_id,
                ConversationLog.origin == "call",
                func.date(ConversationLog.created_at) == func.current_date(),
            )
        )).scalar() or 0
    except Exception as e:  # noqa: BLE001 —— 数不出来就不劝，不拦这一轮
        logger.debug("唤醒提醒：读调用日志失败（非致命）: %s", e)
        return 0


async def deliver_reminder(db, agent, group_id: int) -> bool:
    """到点就往账本落一条劝告，返回是否落了。同一段上下文同一天只落一次。"""
    threshold = int(getattr(settings, "proxy_wake_remind_threshold", 0) or 0)
    if threshold <= 0 or not group_id:
        return False

    from app.models.agent import AgentHistoryEntry
    from app.services.history import history_service

    ref = f"group:{group_id}"
    remind_ref = REMIND_REF_PREFIX + _today_suffix()

    try:
        exists = (await db.execute(
            select(AgentHistoryEntry.id).where(
                AgentHistoryEntry.agent_id == agent.id,
                AgentHistoryEntry.context_ref == ref,
                AgentHistoryEntry.ref == remind_ref,
            ).limit(1)
        )).scalars().first()
        if exists:
            return False
    except Exception as e:  # noqa: BLE001 —— 查不到就当没劝过，宁可多劝一次也不重复刷屏
        logger.debug("唤醒提醒：查账本失败（非致命）: %s", e)

    # 日志里已有的次数：这一轮还没写进去，所以"已有 3 次"意味着这是第 4 次唤醒
    count = await _wakes_today(db, agent.id, group_id)
    if count < threshold:
        return False

    note = (
        f"⚠️ 系统提醒：今天这个群已经把你叫起来 {count} 次了。\n"
        "如果这些消息大多不需要你开口，可以给自己设个界限："
        "按人静音（silence_member）、整群短时勿扰（mute_group / set_dnd）、"
        "放慢你的尺时间（update_ruler_time），或者写一条静默规则（决策代理的 silent）。\n"
        "别把一切都挡掉——@ 你、群公告、特别关心仍然该叫得醒你。"
    )

    try:
        await history_service.append(db, agent.id, ref, [{
            "kind": "note",
            "actor": "system",
            "content": note,
            "ref": remind_ref,
            "flags": {"drop_on_unlock": True},
        }])
    except Exception as e:  # noqa: BLE001 —— 落不进去这一轮就不劝
        logger.warning("唤醒提醒：写账本失败，本轮不劝: %s", e)
        return False

    logger.info(f"AI({agent.id}) 群 {group_id} 今日第 {count} 次唤醒，往账本投一句界限建议")
    return True
