"""
pop_state 工具 — 结束当前状态帧，选择性回跳

支持两种用法：
1. 不传 target_frame_id：回到上一层（LIFO）
2. 传 target_frame_id：直接回到指定状态帧，中间帧归档并在摘要中汇报
   （例如：写代码 → 回消息 → 处理闹钟，闹钟处理完想直接回写代码，
    传 file_work 帧的 id，跳过"回消息"层）
"""
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from app.tools.base import ToolPlugin, ToolRegistry
from app.services.agent.state_stack_service import pop_state

logger = logging.getLogger(__name__)


class PopState(ToolPlugin):
    name = "pop_state"
    description = (
        "结束当前状态帧（只这一帧），回到上一层继续之前的任务；带 target_frame_id 就回到指定那帧。\n"
        "别的帧一个都不动——想去哪帧就把它拉成当前帧，链上其余帧的顺序不变。\n"
        "当前任务完成想回去用这个；不做了想放弃用 close_state；只是离开一会儿、之后还要回到现在"
        "这一帧，用 enter_state。"
    )
    segment = "self_management"
    parameters = {
        "target_frame_id": {
            "type": "string",
            "description": "目标状态帧 id（可选）。多层嵌套想直接回到某一层时传；"
                           "不传则回上一层。可用 list_states 查看帧 id。",
            "nullable": True,
        },
    }
    states = ["active", "dnd", "inactive"]

    async def execute(self, db: AsyncSession, agent_id: int, group_id: int | None,
                      arguments: dict, context: dict) -> dict:
        target = str(arguments.get("target_frame_id") or "").strip()
        stack, msg = await pop_state(db, agent_id, target_frame_id=target)
        await db.commit()
        return {
            "success": True,
            "message": msg,
            "remaining_depth": len(stack),
            "next_frame": stack[-1] if stack else None,
        }


ToolRegistry.register(PopState)
