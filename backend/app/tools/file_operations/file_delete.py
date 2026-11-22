"""
file_delete 工具 — AI 删除自己的文件
"""
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from app.tools.base import ToolPlugin, ToolRegistry

logger = logging.getLogger(__name__)


class FileDelete(ToolPlugin):
    name = "file_delete"
    description = "删除你文件空间中的一个文件。"
    segment = "file_operations"
    parameters = {
        "path": {"type": "string", "description": "要删除的文件路径（相对于你的文件空间根目录）"},
    }
    required = ["path"]
    states = ["active", "dnd"]
    admin_description = "删除自己工作文件夹中的文件。AI 清理不需要的资料时调用，操作不可逆。"
    trigger_condition = "AI 清理不需要的文件时"

    async def execute(self, db: AsyncSession, agent_id: int, group_id: int | None,
                      arguments: dict, context: dict) -> dict:
        from app.services.content.file_service import ai_delete_file

        path = arguments["path"]
        from app.services.agent import knowledge_service as kb
        try:
            await kb.ensure_ai_can_edit(db, agent_id, path)
        except ValueError as e:
            return {"error": True, "message": str(e)}
        try:
            await ai_delete_file(db, agent_id, path)
            return {"success": True, "path": path, "message": "文件已删除"}
        except ValueError as e:
            return {"error": True, "message": str(e)}
        except Exception as e:
            logger.error(f"file_delete 失败: {e}", exc_info=True)
            return {"error": True, "message": f"删除失败：{e}"}


ToolRegistry.register(FileDelete)
