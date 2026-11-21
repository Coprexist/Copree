"""计划板投递 —— 把「这个状态下我排了什么」补成账本条目。

写入侧（`set_alarm` / `update_alarm` / `cancel_alarm` / `fire_alarm`）完全不动：
变没变，用账本里该会话最后一条 plan 条目的正文和这次渲染的板子逐字节比就知道
（详见 docs/dev/plan_and_alarm.md §3.2）。

挂在哪：构建提示词时、紧挨着当轮新消息**之前**落账本。计划到点叫醒的就是这个状态的请求体，
所以板子必须在那个请求体里；位置在历史尾部，不会打掉前缀缓存。
"""
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alarm import AgentAlarm
from app.utils.pure.plan_entry import board_of, make_plan_entry, plan_board, plan_ref

logger = logging.getLogger(__name__)


async def deliver_plans(db: AsyncSession, agent, context_ref: str) -> list[dict]:
    """渲染计划板并与上次投出去的比，不同才补一条（返回刚写进去的）。

    开关也在这里判：`plan_injection_enabled` 是"这个 AI 要不要看见自己的计划"的唯一入口，
    调用方（两条提示词路径）只管调，不重复判断。
    """
    if not context_ref or not getattr(agent, "plan_injection_enabled", False):
        return []
    from app.services.agent.state_stack_service import get_frames
    from app.services.history import history_service
    from app.services.history.context_sync import append_events

    try:
        # 一条闹钟行都没有 = 这个 AI 从来不排计划，板上永远不会有内容：先短路，省掉每轮
        # 读一遍整本账（开关对全量打开后这笔开销每轮都要付）。取消/已执行的闹钟行都还在，
        # 所以"零行"不会是"投过、后来都取消了"——那种要回一句"已经没有计划了"，走下面那条路。
        alarms = (await db.execute(
            select(AgentAlarm).where(AgentAlarm.agent_id == agent.id)
        )).scalars().all()
        if not alarms:
            return []

        ledger = await history_service.read(db, agent.id, context_ref)
        ref = plan_ref(context_ref)
        delivered = ""
        for entry in ledger or []:
            if str((entry or {}).get("ref") or "") == ref:
                delivered = board_of((entry or {}).get("content") or "")

        frames = await get_frames(db, agent.id)
        board = plan_board(
            alarms, context_ref=context_ref,
            current_frame_id=str((frames[-1] if frames else {}).get("id") or ""),
            frames=frames, was_delivered=bool(delivered),
        )
        if board == delivered:
            return []
        event = make_plan_entry(context_ref, board, changed=bool(delivered))
        return await append_events(db, agent, context_ref, [event])
    except Exception as e:
        logger.warning(f"计划板投递失败（非致命）: {e}")
        return []
