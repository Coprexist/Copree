"""
manage_workspace 工具 — AI 读写个人工作区文件（TODO/PLAN/JOURNAL）
"""
import logging
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from app.tools.base import ToolPlugin, ToolRegistry
from app.utils.pure.timeutil import utc_now

logger = logging.getLogger(__name__)


class ManageWorkspace(ToolPlugin):
    name = "manage_workspace"
    description = (
        "管理你的个人工作区文件。你可以读取或写入三个文件：\n"
        "- todo: 你的待办事项列表（markdown 格式）。push_state 时会自动追加待办，完成时标记 [x]\n"
        "- plan: 你的中长期规划文档\n"
        "- journal: 你的操作日志/日记。push_state/pop_state 时自动追加记录，无需手动写\n"
        "用法示例：读 TODO → action='read' file='todo'；写 PLAN → action='write' file='plan' content='...'"
    )
    segment = "self_management"
    parameters = {
        "action": {"type": "string", "enum": ["read", "write"], "description": "read=读取文件内容, write=写入/覆盖文件内容"},
        "file": {"type": "string", "enum": ["todo", "plan", "journal"], "description": "要操作的文件：todo=待办列表, plan=规划文档, journal=日记"},
        "content": {"type": "string", "nullable": True, "description": "要写入的内容（action=write 时必填，markdown 格式）。journal 写入时自动在前面加日期标题。"},
    }
    required = ["action", "file"]
    states = ["active", "dnd", "inactive"]
    admin_description = "管理工作区：添加/移除/排序任务、更新任务状态。AI 的项目管理和任务规划工具。"
    trigger_condition = "AI 规划、调整或执行任务时"

    async def execute(self, db: AsyncSession, agent_id: int, group_id: int | None,
                      arguments: dict, context: dict) -> dict:
        from app.services.agent.workspace_service import get_workspace_file, set_workspace_file

        action = arguments["action"]
        file_type = arguments["file"]

        try:
            if action == "read":
                content = await get_workspace_file(db, agent_id, file_type)
                if not content:
                    return {"success": True, "action": "read", "file": file_type,
                            "content": f"（{file_type} 文件目前为空，你可以开始写第一条）"}
                return {"success": True, "action": "read", "file": file_type, "content": content}

            elif action == "write":
                content = arguments.get("content", "")
                if not content:
                    return {"error": True, "message": "写入内容不能为空"}
                if file_type == "journal":
                    now = utc_now().strftime("%Y-%m-%d %H:%M UTC")
                    existing = await get_workspace_file(db, agent_id, "journal")
                    if existing:
                        content = f"## {now}\n\n{content}\n\n---\n\n{existing}"
                    else:
                        content = f"## {now}\n\n{content}\n"
                await set_workspace_file(db, agent_id, file_type, content)
                await db.commit()
                return {"success": True, "action": "write", "file": file_type,
                        "message": f"已更新 {file_type}", "length": len(content)}

        except Exception as e:
            logger.error(f"manage_workspace 失败 (agent={agent_id}): {e}", exc_info=True)
            return {"error": True, "message": f"工作区操作失败: {e}"}


ToolRegistry.register(ManageWorkspace)
