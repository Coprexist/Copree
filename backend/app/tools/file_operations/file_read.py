"""
file_read 工具 — AI 读取自己的文件
"""
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from app.tools.base import ToolPlugin, ToolRegistry

logger = logging.getLogger(__name__)


class FileRead(ToolPlugin):
    name = "file_read"
    description = ("读取自己文件空间中的一个文本文件。路径相对你的文件空间根（开头的 / 同义）；"
                   "此空间即 run_script 的工作目录，两边文件同一。")
    segment = "file_operations"
    parameters = {
        "path": {"type": "string", "description": "要读取的文件路径（相对于你的文件空间根目录）"},
        "start_line": {"type": "integer", "description": "起始行号（从 1 开始，不传则从第 1 行开始）", "default": 1},
        "end_line": {"type": "integer", "description": "结束行号（包含此行，不传则读到末尾）", "default": -1},
    }
    required = ["path"]
    states = ["active", "dnd"]
    admin_description = "读取自己的文件内容。AI 查看工作笔记、代码、数据或其他持久化资料时调用。"
    trigger_condition = "AI 需要读取文件内容时"

    async def execute(self, db: AsyncSession, agent_id: int, group_id: int | None,
                      arguments: dict, context: dict) -> dict:
        from app.services.content.file_service import ai_read_file

        path = arguments.get("path", "")
        start_line = arguments.get("start_line", 1)
        end_line = arguments.get("end_line", -1)
        try:
            content = await ai_read_file(db, agent_id, path)
            if start_line != 1 or end_line != -1:
                lines = content.split("\n")
                if not lines or lines == [""]:
                    lines = []
                # start_line/end_line 都是 1-indexed
                s = max(0, start_line - 1)
                e = min(len(lines), end_line if end_line > 0 else len(lines))
                if s >= len(lines):
                    return {"success": True, "path": path, "content": "", "total_lines": len(lines)}
                sliced = lines[s:e]
                content = "\n".join(sliced)
            return {"success": True, "path": path, "content": content}
        except ValueError as e:
            return {"error": True, "message": str(e)}
        except Exception as e:
            logger.error(f"file_read 失败: {e}", exc_info=True)
            return {"error": True, "message": f"读取失败：{e}"}


ToolRegistry.register(FileRead)
