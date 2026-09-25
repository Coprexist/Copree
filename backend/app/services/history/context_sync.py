"""群聊历史 → 账本：请求体里「用户/外界带来了什么」的唯一入口

设计见 docs/dev/conversation_history.md §0/§4：
- 请求体 = [锁定 system 段] + [账本历史] + [尾部读数]，历史段内**只追加**；
- 水位从账本自己推（最后一条 message 条目的 ref = 消息 id），不另立游标；
- 窗口装不下的旧消息折成**缺口条目**，与这批**同一次 append**、排在最前面；
- 幂等：没有新消息就一条不写，渲染出的还是同一份字节（前缀命中）。

旧实现（build_messages 里按"最新 N 条"重建窗口）每轮让前缀前移 → 整段 miss，这里是那条的替代。
"""
from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.chat import gm
from app.services.history import history_service
from app.utils.pure.history import gap_entry, latest_message_ref
from app.utils.pure.prompting import keep_newest_within

logger = logging.getLogger(__name__)

# 一批最多带多少字符（与旧窗口同口径：40000 字）
BATCH_MAX_CHARS = 40_000


def context_ref(*, group_id: int | None = None, session_id: str | None = None) -> str:
    """这个会话在账本里的键——只在这里拼一次，别处别再拼。

    群 = `group:{id}`；私信 = `session_id`（就是 `40_90` 那种，与状态帧的 context_ref 同口径）。
    关键字参数是故意的：调用点必须说清自己在哪个会话，别靠位置参数猜。
    """
    if session_id:
        return session_id
    return f"group:{group_id}"


async def append_events(db: AsyncSession, agent, context_ref: str, events: list[dict]) -> list[dict]:
    """一次性事件（能力变更通知 / 便签撤下 / 建议回复）落成账本条目。

    为什么非要落库：这些事件以前只 append 进当轮 messages，说完就没了——下一轮 AI 又按旧表述办事。
    返回刚写入的条目，调用方要把它们接着渲染进**本轮**请求（账本读过的那段不变，事件永远在末尾）。
    """
    events = [e for e in (events or []) if (e.get("content") or "").strip()]
    if not events:
        return []
    return await history_service.append(db, agent.id, context_ref, events)


async def rewrite_context(db: AsyncSession, agent, context_ref: str, *,
                          summary: str, keep_last: int) -> list[dict]:
    """**解锁点**重写整段账本：摘要 + 原样搬运的事件 + 最近 keep_last 条。

    全仓唯一允许动中段的地方（§0：compact / 超时压缩是唯一重写点）；其它路径只能 append。
    事件类（缺口/补看/便签/通知）不揉进摘要，原样搬到摘要之后——它们是契约，不是内容。
    账本空就什么都不做（不动 = 不误清）。
    """
    from app.utils.pure.history import is_compressible, make_entry

    entries = await history_service.read(db, agent.id, context_ref)
    if not entries:
        return []
    keep = entries[-keep_last:] if keep_last > 0 else []
    kept_seqs = {e["seq"] for e in keep}
    events = [e for e in entries if e["seq"] not in kept_seqs and not is_compressible(e)]
    head = [make_entry("summary", summary)] if (summary or "").strip() else []
    await history_service.clear(db, agent.id, context_ref)
    rewritten = await history_service.append(db, agent.id, context_ref, head + events + keep)
    logger.info(
        f"Agent({agent.id}) 会话 {context_ref} 解锁重写：{len(entries)} → {len(rewritten)} 条"
        f"（摘要 {len(head)} + 事件 {len(events)} + 保留 {len(keep)}）"
    )
    return rewritten


async def sync_dm_history(db: AsyncSession, agent, session_id: str, *, cap: int) -> list[dict]:
    """把水位之后的新私信补进账本，返回**整段**历史条目（与群聊同一套语义）。

    cap = 一批最多几条（旧窗口的 limit）。私信不按字符二次裁剪（与旧路径同口径）。
    """
    from sqlalchemy import select as sa_select

    from app.chat import dm as dm_api
    from app.models.dm import DMMessage

    ref = context_ref(session_id=session_id)
    entries = await history_service.read(db, agent.id, ref)
    watermark = latest_message_ref(entries)

    rows = (await db.execute(
        sa_select(DMMessage).where(DMMessage.session_id == session_id, DMMessage.id > watermark)
        .order_by(DMMessage.id.desc()).limit(cap)
    )).scalars().all()
    rows = sorted(rows, key=lambda m: m.id)  # 按 id 归正（与群聊同一口径）

    skipped = 0
    if rows:
        from sqlalchemy import func as sa_func
        q = sa_select(sa_func.count(DMMessage.id)).where(
            DMMessage.session_id == session_id, DMMessage.id < rows[0].id)
        if watermark:
            q = q.where(DMMessage.id > watermark)
        skipped = (await db.execute(q)).scalar() or 0

    batch: list[dict] = []
    if skipped:
        batch.append(gap_entry(skipped, ref=str(rows[0].id)))
    if rows:
        names = await dm_api.resolve_dm_sender_names(db, rows)
        agent_name = getattr(agent, "name", "") or ""
        agent_user_id = getattr(agent, "user_id", None)
        for m in rows:
            batch.append(dm_api.dm_message_entry(
                m, agent_name=agent_name, agent_user_id=agent_user_id,
                sender_name=names.get(m.sender_id),
            ))
    if batch:
        entries = entries + await history_service.append(db, agent.id, ref, batch)
    return entries


async def sync_group_history(db: AsyncSession, agent, group_id: int, *, cap: int, max_len: int) -> list[dict]:
    """把水位之后的新群消息补进账本，返回**整段**历史条目（供渲染请求体）。

    cap = 一批最多几条（旧窗口的 max_unread），max_len = 单条展示上限（群设置的 max_msg_display_len）。
    """
    ref = context_ref(group_id=group_id)
    entries = await history_service.read(db, agent.id, ref)
    watermark = latest_message_ref(entries)

    rows = await gm.get_gm_messages(db, group_id, limit=cap, after_id=watermark or None)
    # 顺序按 **id** 归正：水位就是按 id 记的，而 get_gm_messages 在 after_id 有值时返回倒序
    # （chronological 靠时间戳判先后，同一秒插入的两条判不出来——实测踩过）
    rows = keep_newest_within(sorted(rows, key=lambda m: m.id), BATCH_MAX_CHARS)

    # 水位与本批第一条之间被窗口/字符上限吃掉的：折成缺口，排在这批最前面
    skipped = await gm.count_messages_between(
        db, group_id, after_id=watermark, before_id=rows[0].id) if rows else 0
    batch: list[dict] = []
    if skipped:
        batch.append(gap_entry(skipped, ref=str(rows[0].id)))
    if rows:
        names = await gm.resolve_speaker_names(db, rows)
        agent_name = getattr(agent, "name", "") or ""
        agent_user_id = getattr(agent, "user_id", None)
        for m in rows:
            batch.append(gm.gm_message_entry(
                m, agent_name=agent_name, agent_user_id=agent_user_id,
                speaker_name=names.get((m.sender_type, m.sender_id)), max_len=max_len,
            ))
    if batch:
        entries = entries + await history_service.append(db, agent.id, ref, batch)
    return entries