"""跨状态便签服务 —— DB 编排（纯逻辑在 utils/pure/cross_state_note.py）。

一处写、一处投：
- 写：AI 通过 cross_state_note 工具留一句（带上「我当时在哪个会话」）；
- 投：构建提示词时 sync_frame_notes —— 有效期内投进当前会话，抄一份进状态帧，
  之后每轮字节一致地待在前缀里（缓存命中），直到那段对话 compact/clear 解锁重建。

投递是**一次性过户**：抄进状态帧之后就归那段会话的上下文管（锁），记录侧只剩"还能不能投"
这一件事（记录可以照常过期剪枝、删除、清空，都不影响已经投出去的那份）。
"""
import json
import logging
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.agent import Agent
from app.utils.pure.cross_state_note import (
    make_note, is_expired, live_notes, deliverable_notes, note_copy, notes_brief, MAX_NOTE_CHARS,
)

logger = logging.getLogger(__name__)


async def _load(db: AsyncSession, agent_id: int) -> tuple[list[dict], int]:
    """读便签 + 当前调用刻度（刻度就是投递时效的钟）。"""
    row = (await db.execute(
        select(Agent.cross_state_notes, Agent.llm_call_count).where(Agent.id == agent_id)
    )).first()
    if row is None:
        return [], 0
    notes = row[0] if isinstance(row[0], list) else []
    return list(notes), int(row[1] or 0)


async def _save(db: AsyncSession, agent_id: int, notes: list[dict]) -> None:
    await db.execute(
        text("UPDATE agents SET cross_state_notes = :notes WHERE id = :aid"),
        {"notes": json.dumps(notes, ensure_ascii=False), "aid": agent_id},
    )


async def _origin(db: AsyncSession, agent_id: int) -> tuple[str, str]:
    """便签的「从哪来」：当前活跃状态帧的 context_ref / label（没有帧就留空）。"""
    from app.services.agent.state_stack_service import list_states

    stack = await list_states(db, agent_id)
    if not stack:
        return "", ""
    top = stack[-1]
    return str(top.get("context_ref") or ""), top.get("label") or top.get("context_ref") or ""


async def add_note(db: AsyncSession, agent_id: int, text: str, kind: str = "todo") -> tuple[list[dict], str]:
    notes, call_count = await _load(db, agent_id)
    notes = live_notes(notes, call_count)
    if not (text or "").strip():
        return notes, "便签内容不能为空"
    context_ref, label = await _origin(db, agent_id)
    note = make_note(text, from_context_ref=context_ref, from_label=label,
                     call_count=call_count, kind=kind)
    notes.append(note)
    await _save(db, agent_id, notes)
    logger.info(f"AI({agent_id}) 留了跨状态便签 {note['id']}: {note['text'][:40]}")
    return notes, f"已记下便签 {note['id']}（{note['expires_at_call'] - call_count} 次 API 调用内有效；别的会话拿到后会一直留在它的上下文里）"


async def update_note(db: AsyncSession, agent_id: int, note_id: str,
                      text: str = "", kind: str = "") -> tuple[list[dict], str]:
    notes, call_count = await _load(db, agent_id)
    notes = live_notes(notes, call_count)
    note = next((n for n in notes if n.get("id") == note_id), None)
    if note is None:
        return notes, f"没找到便签 {note_id}（已被删掉或从未存在）"
    if is_expired(note, call_count):
        return notes, f"便签 {note_id} 已过期（只还留在已收到它的会话里，改不动了）"
    if text.strip():
        note["text"] = text.strip()[:MAX_NOTE_CHARS]
    if kind:
        note["kind"] = kind
    await _save(db, agent_id, notes)
    return notes, f"便签 {note_id} 已更新"


async def remove_note(db: AsyncSession, agent_id: int, note_id: str) -> tuple[list[dict], str]:
    """删记录 = 不再投给别人 + 把已经投出去的那份标成「已撤下」（不删行：前缀只变一次）。"""
    from app.services.agent.state_stack_service import retire_frame_notes

    notes, call_count = await _load(db, agent_id)
    live = live_notes(notes, call_count)
    kept = [n for n in live if n.get("id") != note_id]
    if len(kept) == len(live):
        return live, f"没找到便签 {note_id}"
    await _save(db, agent_id, kept)
    retired = await retire_frame_notes(db, agent_id, {note_id})
    return kept, f"已删掉便签 {note_id}" + (f"（{retired} 处会话里已标为撤下）" if retired else "")


async def clear_notes(db: AsyncSession, agent_id: int) -> tuple[list[dict], str]:
    from app.services.agent.state_stack_service import retire_frame_notes

    notes, _ = await _load(db, agent_id)
    await _save(db, agent_id, [])
    retired = await retire_frame_notes(
        db, agent_id, {n.get("id") for n in notes if isinstance(n, dict)})
    return [], "便签已清空" + (f"（{retired} 处会话里已标为撤下）" if retired else "")


async def list_notes(db: AsyncSession, agent_id: int) -> list[dict]:
    notes, call_count = await _load(db, agent_id)
    return notes_brief(notes, call_count)


async def sync_frame_notes(db: AsyncSession, agent_id: int, context_ref: str) -> list[dict]:
    """构建提示词时调用：把该投给本会话的便签投进来；返回本会话已固化的那份。

    投递是**一次性过户**：投进来那一刻抄进状态帧，此后它归这段会话的
    上下文管（锁），记录侧对它没有任何权力——过期、清理、删除都不撤。所以这里只在"有新便签"
    时写一次帧；已投过的一律原样返回（不查记录、不回写，前缀字节天然稳定）。
    它唯一的退出点 = 这段对话 compact / clear。
    """
    from app.services.agent.state_stack_service import list_states, set_frame_notes

    stack = await list_states(db, agent_id)
    if not stack or str(stack[-1].get("context_ref")) != str(context_ref):
        return []

    copies = stack[-1].get("notes") or []
    notes, call_count = await _load(db, agent_id)
    fresh = deliverable_notes(notes, context_ref, call_count, [c.get("id") for c in copies])
    if not fresh:
        return copies
    copies = copies + [note_copy(n) for n in fresh]
    await set_frame_notes(db, agent_id, context_ref, copies)
    logger.info(f"AI({agent_id}) 向会话 {context_ref} 投递跨状态便签 {[n['id'] for n in fresh]}")
    return copies
