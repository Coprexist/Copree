"""
file_edit 工具 — AI 增量编辑文件（查找替换 / 行后插入）
不需要重写整个文件，节省 token，适合大文件小幅修改。
"""
import logging
import os
from sqlalchemy.ext.asyncio import AsyncSession
from app.tools.base import ToolPlugin, ToolRegistry

logger = logging.getLogger(__name__)


class FileEdit(ToolPlugin):
    name = "file_edit"
    description = (
        "增量编辑文件（查找替换 / 行后插入 / 删除行），比 file_write 更省 token。"
        "**重要**：编辑前请先用 file_read 读取文件确认当前内容（至少看关键行），"
        "避免行号偏移导致改错位置。"
        "多次插入时请从最后一行开始往前面插。"
    )
    segment = "file_operations"
    parameters = {
        "path": {
            "type": "string",
            "description": "文件路径（相对于你的文件空间根目录）",
        },
        "operation": {
            "type": "string",
            "enum": ["str_replace", "insert", "delete_lines"],
            "description": (
                "操作类型：\n"
                "- str_replace：精确字符串替换。先读取文件确认原文存在且唯一，再执行替换。\n"
                "- insert：在指定行号**之后**插入新内容。行号 1 开头。多次插入时请从最大行号开始往小插。\n"
                "- delete_lines：删除指定行范围（包含两端）。"
            ),
        },
        "old_string": {
            "type": "string",
            "description": "（str_replace 必填）要被替换的精确原文。必须完全匹配文件中某一段且唯一，否则拒绝执行。",
        },
        "new_string": {
            "type": "string",
            "description": "替换后的新内容（str_replace）或要插入的新内容（insert）。",
        },
        "insert_line": {
            "type": "integer",
            "description": "（insert 必填）在此行号**之后**插入。行号从 1 开始。0 = 插入到文件开头。",
        },
        "start_line": {
            "type": "integer",
            "description": "（delete_lines 必填）要删除的起始行号（从 1 开始，包含此行）",
        },
        "end_line": {
            "type": "integer",
            "description": "（delete_lines 必填）要删除的结束行号（包含此行）",
        },
    }
    # operation 不再必填：只给 old_string/new_string 时按 str_replace 处理（与同目录工具共用推断）
    required = ["path"]
    states = ["active", "dnd"]
    admin_description = "增量编辑自己的文件（查找替换或行后插入），避免大文件全量重写的 token 开销。"
    trigger_condition = "AI 需要修改已有文件的局部内容时"

    async def execute(self, db: AsyncSession, agent_id: int, group_id: int | None,
                      arguments: dict, context: dict) -> dict:
        from app.services.content.file_service import ai_read_file, ai_write_file
        from app.utils.pure.file_edit import infer_operation

        path = arguments["path"]
        operation = infer_operation(arguments)
        new_string = arguments.get("new_string", "")

        try:
            # 1. 读取当前文件内容
            old_content = await ai_read_file(db, agent_id, path)
        except ValueError as e:
            return {"error": True, "message": str(e)}
        except Exception as e:
            logger.error(f"file_edit 读取失败: {e}", exc_info=True)
            return {"error": True, "message": f"读取失败：{e}"}

        from app.utils.pure.file_edit import apply_file_edit

        new_content, err = apply_file_edit(old_content, operation, arguments)
        if err:
            return {"error": True, "message": err}

        # 3. 写回文件
        try:
            metadata = await ai_write_file(db, agent_id, path, new_content, "solo")
            return {
                "success": True,
                "path": metadata.path,
                "size": metadata.size,
                "operation": operation,
            }
        except ValueError as e:
            return {"error": True, "message": str(e)}
        except Exception as e:
            logger.error(f"file_edit 写入失败: {e}", exc_info=True)
            return {"error": True, "message": f"编辑写入失败：{e}"}


ToolRegistry.register(FileEdit)
