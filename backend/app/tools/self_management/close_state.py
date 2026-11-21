"""
close_state 工具 — 关闭状态帧（不恢复上一层）
"""
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from app.tools.base import ToolPlugin, ToolRegistry
from app.services.agent.state_stack_service import close_state

logger = logging.getLogger(__name__)


class CloseState(ToolPlugin):
    name = "close_state"
    description = (
        "关闭当前状态帧或指定帧，不会自动恢复上一任务。"
        "用于'这件事不做了/被叫停了'的场景。"
        "frame_id 为空时关闭当前帧，填 frame_id（从 list_states 获取）则关闭指定帧。"
    )
    segment = "self_management"
    parameters = {
        "frame_id": {
            "type": "string",
            "nullable": True,
            "description": "要关闭的状态帧 ID（从 list_states 获取），为空则关闭当前帧",
        },
    }
    required = []
    states = ["active", "dnd", "inactive"]

    async def execute(self, db: AsyncSession, agent_id: int, group_id: int | None,
                      arguments: dict, context: dict) -> dict:
        frame_id = arguments.get("frame_id", "")
        stack, msg = await close_state(db, agent_id, frame_id)
        await db.commit()
        return {"success": True, "message": msg, "remaining_depth": len(stack)}


ToolRegistry.register(CloseState)
