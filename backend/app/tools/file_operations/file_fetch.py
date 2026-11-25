"""
file_fetch 工具 — 把别人发来的附件收进自己的文件空间
"""
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.tools.base import ToolPlugin, ToolRegistry

logger = logging.getLogger(__name__)


class FileFetch(ToolPlugin):
    name = "file_fetch"
    description = (
        "把别人发给你的文件（消息附件）收进你自己的文件空间。群聊与私信都能收，任何文件类型都能收"
        "（图片、文档、音频……）。不带 file_id 时取当前会话里最近一条带附件的消息。"
        "收进来之后就能用 file_read / file_share / send_file 正常使用它（包括把图再发出去）。"
        "只是看一眼不用收——当轮消息的图片和文本内容平台已经放进上下文了（历史里只剩 [图片] 标记）；"
        "收是为了留下、转发或二次加工。"
    )
    segment = "file_operations"
    parameters = {
        "file_id": {"type": "integer", "nullable": True,
                    "description": "要收的附件 id（消息附件里的 file_id）。不传 = 当前会话最近一条带附件的消息。"},
    }
    required = []
    states = ["active", "dnd"]
    admin_description = "把收到的附件（图片/文档）收进自己的文件空间，之后可读取、转发给他人。"
    trigger_condition = "AI 想留下或再使用别人发来的附件时"

    async def execute(self, db: AsyncSession, agent_id: int, group_id: int | None,
                      arguments: dict, context: dict) -> dict:
        from app.services.content.file_service import ai_fetch_attachment

        file_id = arguments.get("file_id")
        if file_id is None:
            file_id = await self._latest_attachment(db, group_id, context.get("session_id"))
            if file_id is None:
                return {"error": True, "message": "当前会话里还没有带附件的消息可以收"}
        try:
            result = await ai_fetch_attachment(db, agent_id, int(file_id))
        except ValueError as e:
            return {"error": True, "message": str(e)}
        except Exception as e:
            logger.error(f"file_fetch 失败: {e}", exc_info=True)
            return {"error": True, "message": f"收文件失败：{e}"}
        return {"success": True, **result}

    @staticmethod
    async def _latest_attachment(db: AsyncSession, group_id: int | None, session_id: str | None):
        """当前会话最近一条带附件的消息里的 file_id（私信按会话、群聊按群）"""
        import json

        from sqlalchemy import text

        if session_id:
            sql, params = (
                "SELECT attachments FROM dm_messages WHERE session_id = :sid "
                "AND attachments IS NOT NULL ORDER BY id DESC LIMIT 1",
                {"sid": session_id},
            )
        elif group_id:
            sql, params = (
                "SELECT attachments FROM messages WHERE group_id = :gid "
                "AND attachments IS NOT NULL ORDER BY id DESC LIMIT 1",
                {"gid": group_id},
            )
        else:
            return None
        row = (await db.execute(text(sql), params)).first()
        if not row or not row[0]:
            return None
        payload = row[0]
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError:
                return None
        for att in payload or []:
            if isinstance(att, dict) and att.get("file_id"):
                return int(att["file_id"])
        return None


ToolRegistry.register(FileFetch)
