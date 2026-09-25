"""群聊历史 → 账本：请求体里「用户/外界带来了什么」的唯一入口

设计见 docs/dev/conversation_history.md §0/§4：
- 请求体 = [锁定 system 段] + [账本历史] + [尾部读数]，历史段内**只追加**；
- 水位从账本自己推（最后一条 message 条目的 ref = 消息 id），不另立游标；
- 窗口装不下的旧消息折成**缺口条目**，与这批**同一次 append**、排在最前面；
- 幂等：没有新消息就一条不写，渲染出的还是同一份字节（前缀命中）。

旧实现（build_messages 里按"最新 N 条"重建窗口）每轮让前缀前移 → 整段 miss，这里是那条的替代。
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.chat import gm
from app.services.history import history_service
from app.utils.pure.history import gap_entry, latest_message_ref
from app.utils.pure.prompting import chronological, keep_newest_within

# 一批最多带多少字符（与旧窗口同口径：40000 字）
BATCH_MAX_CHARS = 40_000


def context_ref(group_id: int) -> str:
    """这个会话在账本里的键——只在这里拼一次，别处别再拼。"""
    return f"group:{group_id}"


async def sync_group_history(db: AsyncSession, agent, group_id: int, *, cap: int, max_len: int) -> list[dict]:
    """把水位之后的新群消息补进账本，返回**整段**历史条目（供渲染请求体）。

    cap = 一批最多几条（旧窗口的 max_unread），max_len = 单条展示上限（群设置的 max_msg_display_len）。
    """
    ref = context_ref(group_id)
    entries = await history_service.read(db, agent.id, ref)
    watermark = latest_message_ref(entries)

    rows = await gm.get_gm_messages(db, group_id, limit=cap, after_id=watermark or None)
    rows = keep_newest_within(chronological(rows), BATCH_MAX_CHARS)

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