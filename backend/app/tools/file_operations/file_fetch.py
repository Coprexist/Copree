"""
file_fetch 工具 — 把别人发来的附件收进自己的文件空间

编排层：哪条消息算数、编号怎么算在 services/content/attachment_source，
收取与归属在 services/content/file_service.ai_fetch_attachment。
这里只做三件事：把参数翻成一次选区、逐个收、拼回执。
"""
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.tools.base import ToolPlugin, ToolRegistry

logger = logging.getLogger(__name__)


class FileFetch(ToolPlugin):
    name = "file_fetch"
    description = (
        "把别人发给你的文件（消息附件）收进你自己的文件空间。群聊与私信都能收，任何文件类型都能收"
        "（图片、文档、音频……）。不传参数 = 收最近一条带附件的消息里的全部附件；"
        "一条消息带多个附件时，按上下文里的编号挑（[图片 2] 就是 indexes=[2]），可一次给多个。"
        "收进来之后就能用 file_read / file_share / send_file 正常使用它（包括把图再发出去）。"
        "只是看一眼不用收——当轮消息的图片和文本内容平台已经放进上下文了（历史里只剩 [图片] 标记）；"
        "收是为了留下、转发或二次加工。"
    )
    segment = "file_operations"
    parameters = {
        "file_ids": {"type": "array", "items": {"type": "integer"},
                     "description": "要收的附件 id，可一次给多个（如 [51, 52]）。"},
        "indexes": {"type": "array", "items": {"type": "integer"},
                    "description": "按编号收：当前会话最近一条带附件的消息里第几个附件（1 起，如 [1, 3]）。与 file_ids 二选一。"},
    }
    required = []
    states = ["active", "dnd"]
    admin_description = "把收到的附件（图片/文档）收进自己的文件空间，一次可收多个；之后可读取、转发给他人。"
    trigger_condition = "AI 想留下或再使用别人发来的附件时"

    async def execute(self, db: AsyncSession, agent_id: int, group_id: int | None,
                      arguments: dict, context: dict) -> dict:
        from app.services.content.attachment_source import (
            latest_message_attachments, select_for_fetch,
        )
        from app.services.content.file_service import ai_fetch_attachment

        arguments = arguments or {}
        if arguments.get("file_ids") and arguments.get("indexes"):
            return {"error": True,
                    "message": "file_ids 与 indexes 二选一：前者按 id，后者按消息里的编号。"}

        attachments = await latest_message_attachments(
            db, group_id=group_id, session_id=context.get("session_id"),
        )
        selection = select_for_fetch(
            attachments,
            file_ids=arguments.get("file_ids"),
            indexes=arguments.get("indexes"),
        )
        if selection.missing_indexes:
            have = "、".join(str(n) for n in selection.available_indexes) or "无"
            missing = "、".join(str(n) for n in selection.missing_indexes)
            return {"error": True, "message": (
                f"没有第 {missing} 个附件；最近这条消息可收的编号是：{have}"
            )}
        if selection.is_empty:
            # 两种空不是一回事：没有带附件的消息 vs 那条消息里的附件都收不了
            return {"error": True, "message": (
                "最近这条带附件的消息里没有可收的附件" if attachments
                else "当前会话里还没有带附件的消息可以收"
            )}

        fetched: list[dict] = []
        failed: list[dict] = []
        for file_id in selection.file_ids:
            try:
                fetched.append(await ai_fetch_attachment(db, agent_id, file_id))
            except ValueError as e:
                failed.append({"file_id": file_id, "message": str(e)})
            except Exception as e:  # noqa: BLE001 —— 一份失败不拖累其余，如实回给模型
                logger.error(f"file_fetch 失败（file_id={file_id}）: {e}", exc_info=True)
                failed.append({"file_id": file_id, "message": f"收文件失败：{e}"})

        if not fetched:
            return {"error": True, "message": failed[0]["message"] if failed else "没有可收的附件"}

        result: dict = {"success": True, "count": len(fetched), "fetched": fetched}
        if failed:
            result["failed"] = failed
        return result


ToolRegistry.register(FileFetch)
