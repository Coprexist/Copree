"""经历投递 —— 把「这个上下文还没看过的经历」补进账本（对齐 memory_delivery.py）

一个写口、两处调用，共用一本账：

- `deliver_switch_experiences`：刚切进一个新场景的第一轮，补「刚离开那一场」的最新几行。
  只补那一场，不回溯历史场景、不累积——频繁来回切的时候，进过的行账本里已经有了。
- `deliver_recalled_experiences`：常规轮按最近消息的关键词找，命不中就不投。

去重判据是账本里 `exp:<id>` 的 ref：进过这个上下文的行不再投。`drop_on_unlock` 让这些
条目在解锁（compact / 超时压缩）时离场，所以「compact 之后不算投过」不需要额外状态。
"""
from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_experience import OWNER_AI
from app.services.experience import experience_service as exp_svc
from app.utils.pure.experience_entry import (
    belongs_elsewhere, delivered_experiences, make_experience_entry,
)

logger = logging.getLogger(__name__)


def _group_id_of(scene_key: str) -> int | None:
    """`group:12` → 12；私信、插件帧、世界会话都没有群。"""
    key = str(scene_key or "")
    if key.startswith("group:") and key[6:].isdigit():
        return int(key[6:])
    return None


async def _deliver(
    db: AsyncSession, *, owner_type: str, owner_id: int, context_ref: str,
    scene: str, rows: list[dict], limit: int,
) -> list[dict]:
    """公共投递：滤掉看过的 / 本场自己的，追加进账本，返回刚写进去的条目。"""
    from app.services.history import history_service

    if not rows or limit <= 0:
        return []
    try:
        ledger = await history_service.read(db, owner_id, context_ref)
        seen = delivered_experiences(ledger)
        events: list[dict] = []
        for row in reversed(rows):              # 账本里按旧→新排，读起来才是「先…然后…」
            if len(events) >= limit:
                break
            if str(row.get("id")) in seen:
                continue
            if not belongs_elsewhere(row, scene):
                continue
            # 这个上下文第一次见到经历时把边界说一次，别每行都念
            events.append(make_experience_entry(row, lead=not seen and not events))
        if not events:
            return []
        return await history_service.append(db, owner_id, context_ref, events)
    except Exception as e:
        logger.warning(f"经历投递失败（非致命）: {e}")
        return []


async def deliver_switch_experiences(
    db: AsyncSession, *, owner_type: str = OWNER_AI, owner_id: int,
    context_ref: str, scene: str = "", handoff: dict | None = None, limit: int,
) -> list[dict]:
    """切场景后的第一轮：补刚离开那一场的最新几行；一场行都没有时用工具总账补一行。

    `scene` 是当前帧的 context_ref，`handoff` 是当前帧上还没消费的交接
    （由 `state_stack_service.current_scene` 一并读出）。不是刚切过来就什么都不投——
    否则每轮都会补一遍，额度全花在「上一次切过来的那场」上。
    """
    if limit <= 0 or not handoff or not handoff.get("tail"):
        return []
    scene = scene or context_ref
    try:
        left = handoff.get("from_context_ref") or await exp_svc.latest_other_scene(
            db, owner_type, owner_id, scene)
        if not left or left == scene:
            return []
        rows = await exp_svc.scene_rows(db, owner_type, owner_id, left, limit)
        if not rows:
            await _close_scene(db, owner_type, owner_id, left, handoff.get("from_label") or "")
            rows = await exp_svc.scene_rows(db, owner_type, owner_id, left, limit)
        return await _deliver(db, owner_type=owner_type, owner_id=owner_id,
                              context_ref=context_ref, scene=scene, rows=rows, limit=limit)
    except Exception as e:
        logger.warning(f"切场经历投递失败（非致命）: {e}")
        return []


async def _close_scene(
    db: AsyncSession, owner_type: str, owner_id: int, scene: str, label: str,
) -> None:
    """场景收口的平台兜底：这一场一句 key_note 都没写，就用账本里的工具总账补一行。

    摘要直接就是工具总账（这一场"干了什么"的唯一事实来源）；读账本只在收口时发生一次。
    一行都没有（纯聊天、没调工具）就什么都不写——没有可写的事实时不编。
    世界侧没有账本，不在这里兜底（它在自己那边从 workflow_memory 取）。
    """
    if owner_type != OWNER_AI:
        return
    tools = await exp_svc.scene_tool_ledger(db, owner_type, owner_id, scene)
    if not tools:
        return
    await exp_svc.record(
        db, owner_type=owner_type, owner_id=owner_id, context_ref=scene,
        group_id=_group_id_of(scene), summary=tools, source=exp_svc.SOURCE_TOOL_LEDGER,
        label=label, refs={"scene": scene},
    )


async def deliver_recalled_experiences(
    db: AsyncSession, *, owner_type: str = OWNER_AI, owner_id: int,
    context_ref: str, scene: str = "", limit: int, query: str,
) -> list[dict]:
    """常规轮：按最近消息找相关的经历。命不中就不投，不拿时间序硬凑。"""
    if limit <= 0 or not query.strip():
        return []
    scene = scene or context_ref
    try:
        rows = await exp_svc.search(db, owner_type, owner_id, scene, query, limit)
        return await _deliver(db, owner_type=owner_type, owner_id=owner_id,
                              context_ref=context_ref, scene=scene, rows=rows, limit=limit)
    except Exception as e:
        logger.warning(f"按语境投递经历失败（非致命）: {e}")
        return []
