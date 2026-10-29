"""
list_states 工具 — 查看状态链（所有未完成的状态帧，以及它们排队的顺序）
"""
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from app.tools.base import ToolPlugin, ToolRegistry
from app.services.agent.state_stack_service import get_chain, list_states

logger = logging.getLogger(__name__)


class ListStates(ToolPlugin):
    name = "list_states"
    description = (
        "查看你的状态链——所有帧：还在跑的（含被暂停/跳过的层）、已结束的、待交接的都在。\n"
        "当摘要提示'另有 N 帧未完成'或'待交接 N 帧'、或你想确认自己还有哪些任务挂着时调用。"
        "返回每帧的 id / 类型 / 任务 / 待办 / status，可用 pop_state(target_frame_id=...) 直接跳回某一层，"
        "status 是 ended / retired 的要调 finish_frame 销掉。\n"
        "queue 是它们排队的顺序（**第一个就是正在执行的**，只有它真的在跑）；"
        "执行窗口 max_active 默认 1，即只跑队首那一帧。"
    )
    segment = "self_management"
    parameters = {}
    states = ["active", "dnd", "inactive"]

    async def execute(self, db: AsyncSession, agent_id: int, group_id: int | None,
                      arguments: dict, context: dict) -> dict:
        stack = await list_states(db, agent_id)
        chain = await get_chain(db, agent_id)
        current = chain.get("current")
        frames = [{
            "id": f.get("id"),
            "type": f.get("type"),
            "context_ref": f.get("context_ref") or "",
            "doing": f.get("doing") or f.get("why") or "",
            "todo": f.get("todo") or "",
            "status": f.get("status"),
            # 正在执行的那一帧 = 链尾：不必自己看 status 猜谁在跑
            "executing": bool(current) and f.get("id") == current,
        } for f in stack]
        return {
            "success": True,
            "depth": len(frames),
            "frames": frames,
            # 链上队列：尾在前（第一个就是正在执行的那帧），与执行窗口一起给
            "queue": chain.get("queue") or [],
            "max_active": chain.get("max_active", 1),
        }


ToolRegistry.register(ListStates)
