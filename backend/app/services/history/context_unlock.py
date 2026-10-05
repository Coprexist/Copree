"""压缩 / compact 之后的收尾（解锁点）——唯一入口

压缩只换内存里的 messages；账本不重写的话，下一轮 build_messages 又把原文端回来，压了等于没压。

三条路径共用：空闲压缩、轮内自动压缩、AI 主动 compress_context。
第三条以前漏了这里——2026-10-04 群 69 实测：当轮 13 条，下一轮弹回 120 条。

清单即契约（第四批 d）：docs/dev/conversation_history.md。
"""
from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


# 解锁步骤的**清单**：unlock_context 按它顺序执行并把实际执行的步骤返回，测试拿它跟清单
# 对账（顺序也算）——清单里加了步骤却没人实现会当场 KeyError，不会悄悄半解锁。
UNLOCK_STEPS = (
    "rewrite_history",        # 重写账本（摘要 + 事件 + 最近 N 条）
    "clear_note_copies",      # 便签副本（连同撤下通知）随上下文重建离场
    "reset_trigger_state",    # 触发规则状态复位（scope=frame 在新上下文里从零开始）
    "apply_pending_config",   # 应用挂起的配置
    "apply_pending_changes",  # 能力版本对齐最新（工具定义 + 提示词）
    "apply_environment",      # 环境：写进前缀的那份对齐现值（锁定态只落通知，字节不动）
)


def context_ref_for(conversation_type: str, *, group_id: int | None, session_id: str | None) -> str:
    """本会话在账本里的键（群 vs 私信）——只有这里能把三个参数翻成一个键。"""
    from app.services.history.context_sync import context_ref
    return (context_ref(group_id=group_id) if conversation_type == "group"
            else context_ref(session_id=session_id))


async def unlock_context(db: AsyncSession, agent, *, group_id: int | None, session_id: str | None,
                         conversation_type: str, summary: str,
                         trigger_user_id: int | None = None) -> tuple[str, ...]:
    """解锁点（压缩成功）的一整套收尾：按 UNLOCK_STEPS 顺序执行，返回实际执行的步骤名。"""
    from app.services.history.context_sync import rewrite_context
    from app.services.memory.context_compression_service import DEFAULT_KEEP_LAST_N
    ref = context_ref_for(conversation_type, group_id=group_id, session_id=session_id)

    async def _rewrite_history():
        if ref:
            await rewrite_context(db, agent, ref, summary=summary, keep_last=DEFAULT_KEEP_LAST_N)

    async def _clear_note_copies():
        from app.services.agent.state_stack_service import release_active_frame_notes
        await release_active_frame_notes(db, agent.id)

    async def _reset_trigger_state():
        from app.services.agent.state_stack_service import reset_frame_trigger_state
        await reset_frame_trigger_state(db, agent.id)

    async def _apply_pending_config():
        from app.services.agent.agent_service import apply_pending_config
        await apply_pending_config(db, agent)

    async def _apply_pending_changes():
        # 前缀版本化：compact 解锁，**本状态**的 effective 对齐最新（工具定义 + 提示词 + 记忆索引 + 世界源）
        from app.repositories.capability_repo import SQLAlchemyCapabilityRepository
        from app.services.agent.agent_service import prompt_override_of
        from app.services.capability_versioning import (
            agent_prompt_source, apply_pending_changes, memory_index_source, SOURCE_PLATFORM,
        )
        # 记忆索引与人格源都进锁定段：漏了它们，这个源就永远停在第一版（只有 changelog 提过）。
        # 人格要按"本体还是某个用户的覆盖"选，与拼前缀、发通知同一处判定
        override = await prompt_override_of(db, agent.id, trigger_user_id)
        sources = [SOURCE_PLATFORM,
                   agent_prompt_source(agent.id, override, trigger_user_id),
                   memory_index_source(agent.id)]
        # 世界源也要对齐：不然世界删掉技能后，锁定态的工具数组会一直留着旧定义
        try:
            from app.services.world.world_service import find_worlds_by_entity
            seen = set()
            for entity_type, entity_id in (("group", group_id), ("agent", agent.user_id)):
                if not entity_id:
                    continue
                for w in await find_worlds_by_entity(db, entity_type, entity_id):
                    if w.id not in seen:
                        seen.add(w.id)
                        sources.append(f"world-{w.id}")
        except Exception as e:
            logger.warning(f"解锁时收集世界能力源失败（非致命）: {e}")
        await apply_pending_changes(SQLAlchemyCapabilityRepository(db), agent, sources, state=ref)

    async def _apply_environment():
        # 环境：解锁点把写进前缀的那份对齐到现值。锁定态只落通知、字节不动；这里上下文
        # 本来就重建了，换新零成本。DM 没有环境来源（通道按群服务），传 None 即清空。
        from app.services.agent.state_stack_service import write_frame_env
        from app.services.plugin.environment import environment_snapshot
        current = await environment_snapshot(db, group_id) if group_id else None
        await write_frame_env(db, agent.id, ref, locked=current, notified=current)

    runners = {
        "rewrite_history": _rewrite_history,
        "clear_note_copies": _clear_note_copies,
        "reset_trigger_state": _reset_trigger_state,
        "apply_pending_config": _apply_pending_config,
        "apply_pending_changes": _apply_pending_changes,
        "apply_environment": _apply_environment,
    }
    done: list[str] = []
    for name in UNLOCK_STEPS:
        try:
            await runners[name]()   # 清单里写了却没人实现 → KeyError，当场炸而不是悄悄半解锁
        except Exception:
            # 半解锁要响：哪一步挂的、前面做完了什么（静默半解锁正是便签那次的病根）
            logger.exception(f"解锁步骤 {name} 失败：已完成 {done}，整段解锁未完成（下次会重来）")
            raise
        done.append(name)
    return tuple(done)

