"""焦段服务 —— DB 编排（纯逻辑在 utils/pure/focus.py）。

焦段定义存在 agents.foci（agent 级一份）：会话焦段记着哪些会话属于它，
语义焦段只管一个名字。预置的「所有聊天」不落库，读出来由纯函数兜底注入。

合并是唯一会改动记忆的动作：旧锚点当场换成新锚点，免得记忆指着一个已删的焦段。
"""
import json
import logging

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.utils.pure import focus as pure

logger = logging.getLogger(__name__)


async def _read(db: AsyncSession, agent_id: int) -> list[dict]:
    row = (await db.execute(
        select(Agent.foci).where(Agent.id == agent_id)
    )).scalar_one_or_none()
    return pure.normalize(row)


async def _write(db: AsyncSession, agent_id: int, foci: list[dict]) -> None:
    await db.execute(
        text("UPDATE agents SET foci = :foci WHERE id = :aid"),
        {"foci": json.dumps(pure.storable(foci), ensure_ascii=False), "aid": agent_id},
    )


async def _mutate(db: AsyncSession, agent_id: int, change):
    """读 → 纯函数改 → 写，统一返回 (焦段表, 焦段, 消息)。

    change 收当前焦段，返回 (新焦段, 焦段, 消息) 或 (新焦段, 消息)；
    后者的焦段为 None（改名 / 归入这类动作不产新焦段）。顺序与纯函数保持一致，
    免得调用点各自去记谁在第二位。
    """
    foci = await _read(db, agent_id)
    out = change(foci)
    if len(out) == 3:
        foci, focus, msg = out
    else:
        foci, msg = out
        focus = None
    await _write(db, agent_id, foci)
    return foci, focus, msg


def current_ref(group_id: int | None, context: dict) -> str:
    """当前会话在账本里的键——群用 group:{id}，私信用 session_id（只在这儿拼一次）。"""
    from app.services.history.context_sync import context_ref

    if group_id is None:
        return context_ref(session_id=context.get("session_id"))
    return context_ref(group_id=group_id)


async def load(db: AsyncSession, agent_id: int) -> list[dict]:
    return await _read(db, agent_id)


async def create(db: AsyncSession, agent_id: int, name: str, axis: str = pure.SEMANTIC):
    return await _mutate(db, agent_id, lambda f: pure.add(f, name, axis))


async def rename(db: AsyncSession, agent_id: int, focus_id: str, new_name: str):
    return await _mutate(db, agent_id, lambda f: pure.rename(f, focus_id, new_name))


async def join_session(db: AsyncSession, agent_id: int, focus_id: str, context_ref: str):
    return await _mutate(db, agent_id, lambda f: pure.join(f, focus_id, context_ref))


async def leave_session(db: AsyncSession, agent_id: int, focus_id: str, context_ref: str):
    return await _mutate(db, agent_id, lambda f: pure.leave(f, focus_id, context_ref))


async def anchored_memories(db: AsyncSession, agent_id: int, focus_id: str,
                            limit: int = 50) -> list[dict]:
    """锚在某个焦段上的记忆（合并前要先让 AI 看这两摞都是什么）。

    数量可控（单个 agent 的记忆规模），所以在 Python 里筛，不跟各后端的 JSON 语法较劲。
    """
    from app.models.memory import RoughMemory
    from app.models.structured_record import StructuredRecord

    fid = str(focus_id or "").strip()
    out: list[dict] = []
    rows = (await db.execute(select(RoughMemory).where(
        RoughMemory.owner_type == "ai", RoughMemory.owner_id == agent_id))).scalars().all()
    for row in rows:
        if fid in (row.session_foci or []) or fid in (row.semantic_foci or []):
            out.append({"from": "向量记忆", "title": row.title,
                        "weight": row.value_score, "type": row.mem_type})
    rows = (await db.execute(select(StructuredRecord).where(
        StructuredRecord.agent_id == agent_id))).scalars().all()
    for row in rows:
        if fid in (row.session_foci or []) or fid in (row.semantic_foci or []):
            out.append({"from": "结构记忆", "title": f"{row.category}/{row.sub_key}/{row.field}",
                        "weight": row.value_score, "type": row.mem_type})
    return out[:limit]


async def repoint_memories(db: AsyncSession, agent_id: int, drop_id: str, keep_id: str) -> int:
    """合并后把记忆上的旧锚点换成新锚点：两套记忆一起换，免得留下指向已删焦段的锚。"""
    from app.models.memory import RoughMemory
    from app.models.structured_record import StructuredRecord

    hit = 0
    targets = (
        (RoughMemory, (RoughMemory.owner_type == "ai", RoughMemory.owner_id == agent_id)),
        (StructuredRecord, (StructuredRecord.agent_id == agent_id,)),
    )
    for model, filters in targets:
        rows = (await db.execute(select(model).where(*filters))).scalars().all()
        for row in rows:
            for attr in ("session_foci", "semantic_foci"):
                anchors = list(getattr(row, attr) or [])
                if drop_id in anchors:
                    setattr(row, attr, [keep_id if a == drop_id else a for a in anchors])
                    hit += 1
    if hit:
        logger.info(f"AI({agent_id}) 合并焦段：{hit} 处记忆锚点 {drop_id} → {keep_id}")
    return hit


async def merge(db: AsyncSession, agent_id: int, keep_id: str, drop_id: str):
    """合并两个焦段，并把记忆锚点一起搬过去。"""
    foci, _, msg = await _mutate(db, agent_id, lambda f: pure.merge(f, keep_id, drop_id))
    if msg.startswith("「") and "已并入" in msg:
        moved = await repoint_memories(db, agent_id, drop_id, keep_id)
        if moved:
            msg += f"（{moved} 处记忆锚点已改指）"
    return foci, msg


async def check_anchors(db: AsyncSession, agent_id: int,
                        session_foci, semantic_foci) -> tuple[list[str], list[str], list[str]]:
    """按当前焦段表校验记忆要锚的焦段：返回 (会话轴, 语义轴, 需要告诉 AI 的问题)。

    轴放错的会被挪到对的那一侧（AI 很容易把话题焦段塞进会话轴），不存在的直接丢弃——
    宁可不锚，也不要留一个指着空焦段的锚点。
    """
    foci = await _read(db, agent_id)
    out = {pure.SESSION: [], pure.SEMANTIC: []}
    problems: list[str] = []

    for want_axis, ids in ((pure.SESSION, session_foci), (pure.SEMANTIC, semantic_foci)):
        for fid in pure.normalize_anchor_ids(ids):
            focus = pure.find(foci, fid)
            if focus is None:
                problems.append(f"焦段 {fid} 不存在，已忽略")
                continue
            if focus.get("axis") != want_axis:
                which = "语义" if focus.get("axis") == pure.SEMANTIC else "会话"
                problems.append(f"「{focus['name']}」是{which}焦段，已放到对应的轴上")
            if fid not in out[focus["axis"]]:
                out[focus["axis"]].append(fid)

    return out[pure.SESSION], out[pure.SEMANTIC], problems
