"""记忆投递 —— 把"这个上下文还没看过、或已经过时"的记忆补进账本。

一处写、一处投：记忆的写入侧完全不用改（不插桩、不发事件、不建队列表），差异在
**投递时**对出来——扫一遍本账本里已有的 mem: 条目，和记忆的当前指纹比一比就知道。

挂在哪：构建提示词时，紧挨着当轮新消息**之前**落账本。"先想起这个人，再读他说的
话"，而且只追加：前缀字节从头到尾稳定，整段历史都能吃缓存。
"""
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.memory import DetailMemory, RoughMemory
from app.utils.pure.memory_entry import delivered_memories, make_memory_entry, memory_fingerprint

logger = logging.getLogger(__name__)


def _as_dict(row: RoughMemory, content) -> dict:
    """记忆行 + 正文。正文在 detail_memories（内容层），指纹必须带上它——
    只认标题的话，改正文就等于没改，这条永远不会被更正。"""
    return {
        "id": row.id,
        "title": row.title,
        "content": content or "",
        "session_refs": row.session_refs,
        "session_foci": row.session_foci,
        "semantic_foci": row.semantic_foci,
    }


async def deliver_memories(db: AsyncSession, agent, context_ref: str,
                           recalled_ids: list[int]) -> list[dict]:
    """把本上下文缺的记忆补成账本条目，返回刚写进去的那几条。

    三种情形：账本里没见过这个 id → 投全文；见过但指纹不同 → 投"以新的为准"；
    指纹相同 → 什么都不做。所以记过一次的记忆不会重复投，改过的会补一次，而且
    中间改了几次都只补最新那一版。
    """
    from app.services.history import history_service
    from app.services.history.context_sync import append_events

    try:
        ledger = await history_service.read(db, agent.id, context_ref)
        seen = delivered_memories(ledger)

        recalled = [int(i) for i in recalled_ids if i]
        # 本轮召回的 + 账本里已有的（后者可能早就聊过去了，但改过就得补）
        ids = list(dict.fromkeys(recalled + [int(i) for i in seen if str(i).isdigit()]))
        if not ids:
            return []
        rows = (await db.execute(
            select(RoughMemory, DetailMemory.content)
            .outerjoin(DetailMemory, DetailMemory.rough_id == RoughMemory.id)
            .where(RoughMemory.id.in_(ids))
        )).all()
        current: dict[int, dict] = {}
        for row, content in rows:                   # 一条记忆多份正文时留先读到的那份
            current.setdefault(int(row.id), _as_dict(row, content))

        events: list[dict] = []
        for mem_id in recalled:                     # 召回的按召回顺序投，顺序本身是信息
            mem = current.get(mem_id)
            if mem is None:
                continue
            key = str(mem_id)
            if key not in seen:
                events.append(make_memory_entry(mem))
            elif seen[key] != memory_fingerprint(mem):
                events.append(make_memory_entry(mem, changed=True))
        recalled_set = {str(i) for i in recalled}
        for key, fp in seen.items():                # 没召回到、但改过的：也补一条更正
            if key in recalled_set:
                continue
            mem = current.get(int(key)) if key.isdigit() else None
            if mem is not None and memory_fingerprint(mem) != fp:
                events.append(make_memory_entry(mem, changed=True))

        if not events:
            return []
        return await append_events(db, agent, context_ref, events)
    except Exception as e:
        logger.warning(f"记忆投递失败（非致命）: {e}")
        return []
