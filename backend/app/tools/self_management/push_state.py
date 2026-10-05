"""
push_state 工具 — 切换任务上下文时记录新状态帧
"""
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from app.tools.base import ToolPlugin, ToolRegistry
from app.services.agent.state_stack_service import make_state_frame, push_state
from app.utils.pure.state_stack import current

logger = logging.getLogger(__name__)


class PushState(ToolPlugin):
    name = "push_state"
    description = (
        "切换任务上下文（从群聊切到写代码、从当前对话切到处理私信、设闹钟等）。同一个会话"
        "（context_ref 相同）只有一帧：再进同一个群/私信是**切回原来那帧**（帧身份不变，不叠第二帧），"
        "换到别的会话才压新帧。原任务会被自动标记为'暂停'，完成后可通过 pop_state 恢复。\n"
        "参数：type=任务类型(group_chat/dm/world/file_work/alarm/project/write/other)、"
        "context_ref=上下文引用(如 group:7 / world:45 / dm:12 / file:notes.md)、"
        "why=为什么来做这件事、doing=正在做什么、"
        "todo=待办列表(可选)、plan=执行计划(可选)"
    )
    segment = "self_management"
    parameters = {
        "type": {
            "type": "string",
            "enum": ["group_chat", "dm", "world", "file_work", "alarm", "project", "write", "other"],
            "description": "任务类型（world=在世界里游玩，配合 enter_world 用）",
        },
        "context_ref": {"type": "string", "description": "上下文引用，如 group:7 / world:45 / dm:12 / file:xxx"},
        "why": {"type": "string", "description": "触发原因——为什么来做这件事"},
        "doing": {"type": "string", "description": "正在做什么——当前的行动"},
        "todo": {"type": "string", "nullable": True, "description": "待办列表（文本描述）"},
        "plan": {"type": "string", "nullable": True, "description": "执行计划（可选）"},
    }
    required = ["type", "context_ref", "why", "doing"]
    states = ["active", "dnd", "inactive"]

    async def execute(self, db: AsyncSession, agent_id: int, group_id: int | None,
                      arguments: dict, context: dict) -> dict:
        frame = make_state_frame(
            type_=arguments["type"],
            context_ref=arguments["context_ref"],
            why=arguments["why"],
            doing=arguments["doing"],
            todo=arguments.get("todo", ""),
            plan=arguments.get("plan", ""),
        )
        stack, msg = await push_state(db, agent_id, frame)
        await db.commit()
        # 切回的可能是同会话原有的那帧（身份不变）：给出去的 id 必须是**真正激活的那帧**，
        # 不能是刚构建的这个——它可能压根没进栈
        active = current(stack) or frame
        return {"success": True, "message": msg, "stack_depth": len(stack), "frame_id": active["id"]}


ToolRegistry.register(PushState)
