"""会话历史账本服务 —— DB 编排（纯逻辑在 utils/pure/history.py）

唯一入口：两站（主站 AI / 世界 AI）共用这套语义；世界侧的存储适配器后续接入。
**只追加**：append 失败就整批失败，绝不半写；重写（compress/clear）只由解锁点调用。
"""
import logging

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import AgentHistoryEntry, AgentHistorySeq

logger = logging.getLogger(__name__)


def _row_to_dict(row: AgentHistoryEntry) -> dict:
    return {
        "id": row.id,
        "context_ref": row.context_ref,
        "seq": row.seq,
        "kind": row.kind,
        "actor": row.actor,
        "content": row.content,
        "ref": row.ref or "",
        "flags": dict(row.flags or {}),
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


async def last_of(db: AsyncSession, agent_id: int, context_ref: str, *,
                   kind: str, ref: str = "") -> dict | None:
    """同 kind、同 ref 的**最近一条**——写入时判「这句是不是刚说过」的唯一查询。

    按 (kind, ref) 一起定位：通知、计划板都靠 ref 归属，同 kind 不同 ref 是两件不同的事。
    """
    row = (await db.execute(
        select(AgentHistoryEntry).where(
            AgentHistoryEntry.agent_id == agent_id,
            AgentHistoryEntry.context_ref == context_ref,
            AgentHistoryEntry.kind == kind,
            func.coalesce(AgentHistoryEntry.ref, "") == (ref or ""),
        ).order_by(AgentHistoryEntry.seq.desc()).limit(1)
    )).scalars().first()
    return _row_to_dict(row) if row else None


# 领号：一次把这一批要用的 seq 区间领走，返回区间起点（条目从 base+1 开始排）。
# 首写自播种：计数行不存在时按账本现有的 MAX(seq) 起算，所以迁移不回填、老库也接得上。
# 三个参数都显式 CAST：同一个参数在 INSERT / 子查询 / ON CONFLICT 里被推断成不同类型时，
# PG 会直接报 AmbiguousParameterError。
_RESERVE_SQL = """
    INSERT INTO agent_history_seq (agent_id, context_ref, last_seq)
    VALUES (
        CAST(:a AS INTEGER), CAST(:c AS VARCHAR(64)),
        CAST(:n AS INTEGER) + COALESCE(
            (SELECT MAX(seq) FROM agent_history_entries
              WHERE agent_id = CAST(:a AS INTEGER)
                AND context_ref = CAST(:c AS VARCHAR(64))), 0)
    )
    ON CONFLICT (agent_id, context_ref)
    DO UPDATE SET last_seq = agent_history_seq.last_seq + CAST(:n AS INTEGER)
    RETURNING last_seq - CAST(:n AS INTEGER)
"""


async def _reserve(db: AsyncSession, agent_id: int, context_ref: str, count: int) -> int:
    """向数据库领 count 个连续的 seq，返回这一段的前一号。

    为什么不在 Python 里算：`MAX(seq)+1` 看不见别的事务还没提交的行，两个并发的写者会领到
    同一个号，后到的那个撞唯一索引、原地等先到的提交。先到的要是正等着后到的返回（回合里调
    插件工具就是这个形状），两边就互等——2026-11-07 群 81 冻结 12 小时。
    这条语句把「读 + 加 + 写」交给数据库一次做完，领到的区间只属于自己，写几条都不会撞。
    """
    row = (await db.execute(text(_RESERVE_SQL), {
        "a": int(agent_id), "c": context_ref, "n": int(count),
    })).first()
    return int(row[0] or 0)


async def append(db: AsyncSession, agent_id: int, context_ref: str, entries: list[dict]) -> list[dict]:
    """追加一批条目：seq 在这里统一分配（调用方不用管），批次内顺序即传入顺序。

    缺口事件和它那批消息必须**同一次 append**（缺口在前）——这是不断缓存的前提。
    分配走 `_reserve`（数据库一次领走），所以并发写同一段会话也不会撞号、不会互等。
    """
    entries = [e for e in (entries or []) if (e.get("content") or "").strip()]
    if not entries:
        return []
    base = await _reserve(db, agent_id, context_ref, len(entries))

    out: list[dict] = []
    rows: list[AgentHistoryEntry] = []
    for offset, entry in enumerate(entries, start=1):
        row = AgentHistoryEntry(
            agent_id=agent_id,
            context_ref=context_ref,
            seq=base + offset,
            kind=entry.get("kind") or "message",
            actor=entry.get("actor") or "system",
            content=entry["content"],
            ref=(entry.get("ref") or None),
            # seen（已读）是平台的投递信号——请求真的发出去时由 executor 打，写入方传什么都不认。
            # 这不是安全闸（同进程的调用方想绕总有别的路），是别让照抄示例的人把语义写反。
            flags={k: v for k, v in (entry.get("flags") or {}).items() if k != "seen"},
        )
        db.add(row)
        rows.append(row)
    await db.flush()
    # id 回填：append 的返回值与 read 同形（投递标记按 id 定位，缺 id 就标不上）
    out = [{**entry, "seq": base + offset, "id": row.id}
           for offset, (entry, row) in enumerate(zip(entries, rows), start=1)]
    return out


async def mark_seen(db: AsyncSession, entries: list[dict]) -> int:
    """把**已随请求发出**的条目标为已读（唯一入口）。

    解锁重写丢弃带 drop_on_unlock 的条目时只看这一个标记：投递过的才离场，没投出去的
    原样搬进新账本——失败通知这类"没人唤醒时落的条目"不会在 AI 看见之前被压掉。
    调用点在 LLM 响应回来之后（executor），不在渲染时：build 了却没发出去不算看过。
    """
    ids = [int(e["id"]) for e in (entries or [])
           if e.get("id")
           and (e.get("flags") or {}).get("drop_on_unlock")
           and not (e.get("flags") or {}).get("seen")]
    if not ids:
        return 0
    rows = (await db.execute(
        select(AgentHistoryEntry).where(AgentHistoryEntry.id.in_(ids))
    )).scalars().all()
    for row in rows:
        row.flags = {**(row.flags or {}), "seen": True}
    await db.flush()
    return len(rows)


async def read(db: AsyncSession, agent_id: int, context_ref: str, *,
               since_seq: int | None = None, limit: int | None = None) -> list[dict]:
    """读账本（seq 升序）。since_seq = compact 边界锚点：只取边界之后的部分。"""
    query = select(AgentHistoryEntry).where(
        AgentHistoryEntry.agent_id == agent_id,
        AgentHistoryEntry.context_ref == context_ref,
    )
    if since_seq is not None:
        query = query.where(AgentHistoryEntry.seq > since_seq)
    query = query.order_by(AgentHistoryEntry.seq.asc())
    if limit is not None:
        query = query.limit(limit)
    rows = (await db.execute(query)).scalars().all()
    return [_row_to_dict(r) for r in rows]


async def tail(db: AsyncSession, agent_id: int, context_ref: str, n: int) -> list[dict]:
    """读账本**最新 n 条**（返回仍是 seq 升序）——"读某个会话最近几条"的唯一入口。

    为什么不用 read(limit=)：`read` 的 limit 是从**最早**往后取的（compact 边界用），
    要看尾部必须从后往前取再翻正序。
    """
    rows = (await db.execute(
        select(AgentHistoryEntry).where(
            AgentHistoryEntry.agent_id == agent_id,
            AgentHistoryEntry.context_ref == context_ref,
        ).order_by(AgentHistoryEntry.seq.desc()).limit(max(1, int(n)))
    )).scalars().all()
    return [_row_to_dict(r) for r in reversed(rows)]


async def count(db: AsyncSession, agent_id: int, context_ref: str) -> int:
    return (await db.execute(
        select(func.count(AgentHistoryEntry.id)).where(
            AgentHistoryEntry.agent_id == agent_id,
            AgentHistoryEntry.context_ref == context_ref,
        )
    )).scalar() or 0


async def last_seq(db: AsyncSession, agent_id: int, context_ref: str) -> int:
    return (await db.execute(
        select(func.coalesce(func.max(AgentHistoryEntry.seq), 0)).where(
            AgentHistoryEntry.agent_id == agent_id,
            AgentHistoryEntry.context_ref == context_ref,
        )
    )).scalar() or 0


async def clear(db: AsyncSession, agent_id: int, context_ref: str) -> int:
    """解锁（compact / 超时压缩 / clear）时的重写：整段丢掉。

    只由解锁点调用——锁定态碰它就是断缓存。
    领号计数一并清掉（同一笔事务）：重写后的账本重新从 1 开始，跟"重写"这件事本身的语义一致。
    """
    result = await db.execute(delete(AgentHistoryEntry).where(
        AgentHistoryEntry.agent_id == agent_id,
        AgentHistoryEntry.context_ref == context_ref,
    ))
    await db.execute(delete(AgentHistorySeq).where(
        AgentHistorySeq.agent_id == agent_id,
        AgentHistorySeq.context_ref == context_ref,
    ))
    removed = result.rowcount or 0
    if removed:
        logger.info(f"Agent({agent_id}) 会话 {context_ref} 账本解锁重写：清掉 {removed} 条")
    return removed
