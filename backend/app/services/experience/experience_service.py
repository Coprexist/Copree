"""跨情景经历索引 —— 写与查（投递在 experience_delivery.py）

一行 = 某个 AI 在某一场里干过什么。两种来源：

- `key_note`：轮末 AI 自己写的那句（end_turn.key_note）——「这一轮干了什么」的第一手；
- `tool_ledger` / `ai`：场景收口时平台用工具总账补的兜底，或 AI 用 note_experience 主动写。

归属是 `owner_type + owner_id`：主站 AI（`ai` / agents.id）与世界 AI（`world_ai` / world_ais.id）
共用这一张表与同一套读写，不各自再建一份。

这张表不做向量、不做焦段过滤、不参与衰减：它回答的是「我去过哪、干了什么」，
不是「我还记不记得这个事实」。细节靠 scene_key 回查（search_messages / read_conversation）。
"""
from __future__ import annotations

import logging

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_experience import (
    MAX_SUMMARY_CHARS, OWNER_AI, AgentExperience,
)

logger = logging.getLogger(__name__)

SOURCE_KEY_NOTE = "key_note"
SOURCE_TOOL_LEDGER = "tool_ledger"
SOURCE_AI = "ai"

# 按语境召回时最多看几行候选（投递上限另有闸，见 delivery）
SEARCH_CANDIDATES = 40


def _clip(text: str, limit: int = MAX_SUMMARY_CHARS) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _as_dict(row: AgentExperience) -> dict:
    """行 → 纯函数要的形状。日期只给 MM-DD：经历是「最近干过什么」，年份在这里没信息量。"""
    ended = getattr(row, "ended_at", None) or getattr(row, "created_at", None)
    return {
        "id": row.id,
        "scene_key": row.scene_key or "",
        "scene_label": row.scene_label or "",
        "frame_type": row.frame_type or "",
        "source": row.source or "",
        "summary": row.summary or "",
        "tools": row.tools or "",
        "when_label": ended.strftime("%m-%d") if ended else "",
    }


async def scene_label(
    db: AsyncSession, context_ref: str, group_id: int | None, me_user_id: int = 0,
) -> str:
    """主站场景给人看的名字：群名 / 私信对方。查不到就退回会话键，别让整行写不下去。

    只在写行的时候查一次（一次轮末 / 一次收口），不进每轮路径。
    """
    try:
        if group_id:
            from app.models.group import Group

            name = (await db.execute(
                select(Group.name).where(Group.id == group_id)
            )).scalar_one_or_none()
            return str(name or context_ref)[:100]
        # 私信：session_id 是 "<a>_<b>"，取不是自己的那一方
        parts = str(context_ref or "").split("_")
        if len(parts) == 2 and all(p.isdigit() for p in parts):
            ids = [int(p) for p in parts]
            other_id = ids[0] if ids[1] == int(me_user_id or 0) else ids[1]
            from app.utils.display_name import display_name

            return str(await display_name(db, other_id))[:100]
    except Exception as e:
        logger.warning(f"经历场景名解析失败（用会话键兜底）: {e}")
    return str(context_ref or "")[:100]


async def record(
    db: AsyncSession,
    *,
    owner_type: str = OWNER_AI,
    owner_id: int,
    context_ref: str,
    summary: str,
    source: str = SOURCE_KEY_NOTE,
    group_id: int | None = None,
    me_user_id: int = 0,
    tools: str = "",
    frame_type: str = "",
    refs: dict | None = None,
    label: str = "",
) -> dict | None:
    """写一行经历。摘要为空就什么都不写（空行对谁都没用）。

    `label` 给了就直接用（世界侧有世界名、交接包里有上一场的名字），否则现查。
    """
    body = _clip(summary)
    if not body:
        return None
    try:
        scene_label_ = _clip(label, 100) or await scene_label(
            db, context_ref, group_id, me_user_id)
        row = AgentExperience(
            owner_type=owner_type,
            owner_id=owner_id,
            scene_key=str(context_ref or "")[:64],
            scene_label=scene_label_,
            frame_type=frame_type or ("group_chat" if group_id else "dm"),
            source=source,
            summary=body,
            tools=_clip(tools, 500),
            refs=dict(refs or {}),
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return _as_dict(row)
    except Exception as e:
        # 写经历失败不该拖垮这一轮（它只是「以后能想起」，不是这一轮要做的事）
        logger.warning(f"经历写入失败（非致命）: {e}")
        await db.rollback()
        return None


async def has_rows_for_scene(
    db: AsyncSession, owner_type: str, owner_id: int, scene_key: str,
) -> bool:
    """这一场写过经历没有——场景收口的兜底据此决定要不要补一行。"""
    row = (await db.execute(
        select(AgentExperience.id)
        .where(AgentExperience.owner_type == owner_type,
               AgentExperience.owner_id == owner_id,
               AgentExperience.scene_key == str(scene_key or ""))
        .limit(1)
    )).scalar_one_or_none()
    return row is not None


async def latest_other_scene(
    db: AsyncSession, owner_type: str, owner_id: int, context_ref: str,
) -> str:
    """除本场之外最近出现过的场景键（切场景时「补最新的那场」的依据）。"""
    row = (await db.execute(
        select(AgentExperience.scene_key)
        .where(AgentExperience.owner_type == owner_type,
               AgentExperience.owner_id == owner_id,
               AgentExperience.scene_key != str(context_ref or ""))
        .order_by(AgentExperience.id.desc())
        .limit(1)
    )).scalar_one_or_none()
    return str(row or "")


async def scene_rows(
    db: AsyncSession, owner_type: str, owner_id: int, scene_key: str, limit: int,
) -> list[dict]:
    """某一场最近的几行（新的在前）。"""
    if not scene_key or limit <= 0:
        return []
    rows = (await db.execute(
        select(AgentExperience)
        .where(AgentExperience.owner_type == owner_type,
               AgentExperience.owner_id == owner_id,
               AgentExperience.scene_key == str(scene_key))
        .order_by(AgentExperience.id.desc())
        .limit(limit)
    )).scalars().all()
    return [_as_dict(r) for r in rows]


async def recent(
    db: AsyncSession, owner_type: str, owner_id: int, limit: int, *, exclude_scene: str = "",
) -> list[dict]:
    """最近几行（跨场景，新的在前）——note_experience 的 list 用它看自己记过什么。"""
    if limit <= 0:
        return []
    stmt = select(AgentExperience).where(AgentExperience.owner_type == owner_type,
                                         AgentExperience.owner_id == owner_id)
    if exclude_scene:
        stmt = stmt.where(AgentExperience.scene_key != str(exclude_scene))
    rows = (await db.execute(
        stmt.order_by(AgentExperience.id.desc()).limit(limit)
    )).scalars().all()
    return [_as_dict(r) for r in rows]


async def search(
    db: AsyncSession, owner_type: str, owner_id: int, context_ref: str,
    query: str, limit: int,
) -> list[dict]:
    """按语境找：最近消息里的关键词命中摘要或场景名。

    不投本场的行（`belongs_elsewhere` 的口径在这里先筛一道，省得取回来再丢）；
    命中不足就是不足，不拿时间序硬凑——硬凑会把「按语境」退化成「按时间轮播」。
    """
    if not query.strip() or limit <= 0:
        return []
    from app.services.memory.memory_service import extract_keywords

    words = extract_keywords(query)
    if not words:
        return []
    conds = []
    for w in words:
        like = f"%{w}%"
        conds.append(AgentExperience.summary.ilike(like))
        conds.append(AgentExperience.scene_label.ilike(like))

    rows = (await db.execute(
        select(AgentExperience)
        .where(AgentExperience.owner_type == owner_type,
               AgentExperience.owner_id == owner_id,
               AgentExperience.scene_key != str(context_ref or ""),
               or_(*conds))
        .order_by(AgentExperience.id.desc())
        .limit(min(SEARCH_CANDIDATES, max(limit, SEARCH_CANDIDATES)))
    )).scalars().all()
    return [_as_dict(r) for r in rows][:limit]


async def scene_tool_ledger(
    db: AsyncSession, owner_type: str, owner_id: int, context_ref: str,
) -> str:
    """场景收口的兜底素材：这一段对话账本里的 [本轮工具] 条目，按时间拼起来。

    读账本只在收口时发生（一次），不进每轮路径。世界侧没有账本，不走这里
    （它在自己那边从 workflow_memory 取）。
    """
    try:
        from app.services.history import history_service

        entries = await history_service.read(db, owner_id, str(context_ref or ""))
    except Exception as e:
        logger.warning(f"读账本拼工具总账失败（非致命）: {e}")
        return ""
    lines = [str(e.get("content") or "").strip()
             for e in entries or []
             if (e.get("kind") == "tool")]
    return "；".join(x for x in lines if x)
