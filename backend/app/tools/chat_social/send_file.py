"""
send_file 工具 — AI 将已有文件作为附件引用发送（不复制存储）
"""
import logging
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.tools.base import ToolPlugin, ToolRegistry
from app.utils.text import COLOR_SYNTAX_HINT

logger = logging.getLogger(__name__)


class SendFile(ToolPlugin):
    name = "send_file"
    description = (
        "将文件空间中已有的一个或多个文件作为附件发送到群聊或私信。"
        "文件必须先通过 file_write 创建（会自动注册到文件系统）。"
        "群聊用 group_id，私信用 target_user_id（二选一）。"
        "发送多个文件用 file_paths（数组），单个文件用 file_path（字符串）。"
        "可选附带文字说明 content（支持 Markdown 和彩色文字）。"
    )
    segment = "chat_social"
    parameters = {
        "file_path": {"type": "string", "nullable": True, "description": "单文件路径（如 workspace/report.md）。与 file_paths 二选一。"},
        "file_paths": {"type": "array", "items": {"type": "string"}, "nullable": True, "description": "多文件路径数组（如图片列表 [img1.png, img2.jpg]）。与 file_path 二选一。"},
        "group_id": {"type": "integer", "nullable": True, "description": "目标群聊 ID（群聊时填写）"},
        "target_user_id": {"type": "integer", "nullable": True, "description": "目标用户 ID（私信时填写）"},
        "content": {"type": "string", "nullable": True, "description": f"附带的文字说明（可选，支持 Markdown 和彩色文字）。{COLOR_SYNTAX_HINT}"},
    }
    required = ["file_path"]
    states = ["active"]
    admin_description = "在群聊中发送文件。AI 分享工作成果、资料或图片时调用，支持引用已有文件。"
    trigger_condition = "AI 需要分享文件到群聊时"

    async def execute(self, db: AsyncSession, agent_id: int, group_id: int | None,
                      arguments: dict, context: dict) -> dict:
        from app.models.file import FileMetadata
        from app.services.content.file_service import ai_stored_path
        from app.chat.gm import send_gm_message, gm_message_to_dict
        from app.chat.dm import send_dm_message, get_or_create_dm_session
        from app.models.agent import Agent as AgentModel

        file_path = arguments.get("file_path")
        file_paths = arguments.get("file_paths")
        target_group = arguments.get("group_id", group_id)
        target_user = arguments.get("target_user_id")
        caption = (arguments.get("content") or "").strip()

        # ── 收集文件路径 ──
        paths = []
        if file_paths:
            if isinstance(file_paths, list):
                paths = file_paths
            else:
                paths = [str(file_paths)]
        elif file_path:
            paths = [file_path]
        else:
            return {"error": True, "message": "请提供 file_path（单个文件）或 file_paths（数组，多个文件）"}
        if not paths:
            return {"error": True, "message": "文件路径列表为空"}
        paths = [p.strip() for p in paths if p.strip()]

        # ── 校验：group_id 和 target_user_id 二选一 ──
        if target_group is not None and target_user is not None:
            return {"error": True, "message": "不能同时指定 group_id 和 target_user_id，请二选一"}
        if target_group is None and target_user is None and group_id is None:
            return {"error": True, "message": "请指定 group_id（群聊）或 target_user_id（私信）"}
        if target_group is None:
            target_group = group_id

        # ── 批量查找文件元数据（AI 自己的文件，零拷贝引用） ──
        attachments = []
        missing = []
        for fp in paths:
            result = await db.execute(
                select(FileMetadata).where(
                    FileMetadata.path == ai_stored_path(agent_id, fp),
                    FileMetadata.owner_type == "ai",
                    FileMetadata.owner_id == agent_id,
                )
            )
            meta = result.scalar_one_or_none()
            if not meta:
                missing.append(fp)
            else:
                attachments.append({
                    "file_id": meta.id,
                    "name": fp.rsplit("/", 1)[-1].rsplit("\\", 1)[-1],
                    "path": meta.path,
                    "size": meta.size,
                    "mime_type": meta.mime_type or "application/octet-stream",
                })

        if missing:
            return {
                "error": True,
                "message": (
                    f"以下文件不存在，请先用 file_write 创建: {', '.join(missing)}"
                ),
            }

        # ── AI 名称和头像 ──
        agent_name = context.get("agent_name", f"AI:{agent_id}")
        sender_avatar = None
        agent_user_id = None
        try:
            a_result = await db.execute(select(AgentModel).where(AgentModel.id == agent_id))
            a_obj = a_result.scalar_one_or_none()
            if a_obj:
                sender_avatar = a_obj.avatar_url
                agent_user_id = a_obj.user_id
        except Exception:
            pass

        if agent_user_id is None and target_user is not None:
            return {"error": True, "message": "AI 尚未初始化统一用户 ID"}

        manager = context.get("manager")

        # 出站能力：这个群接的通道带不了附件时（QQ 官方机器人接口没有这个出口），
        # 文件只留在站内、群里的人收不到——必须如实回给 AI，否则它以为发出去了
        channel_note = ""
        if target_user is None:
            try:
                from app.services.plugin.channel import files_supported
                if not await files_supported(db, target_group):
                    channel_note = (
                        "；但本群接的外部通道带不了附件：文件只在站内可见，QQ 那边收不到"
                        "（要传内容请把正文写成文字，或给出能打开的链接）"
                    )
            except Exception as e:  # noqa: BLE001 —— 能力查询失败不该拦住发文件
                logger.warning(f"send_file 通道能力查询失败（按能带附件处理）: {e}")

        if target_user is not None:
            # ── DM 私信（sender_id 用 users 表 ID） ──
            session = await get_or_create_dm_session(db, current_user_id=agent_user_id, target_user_id=target_user)
            if session is None:
                return {"error": True, "message": "无法创建 DM 会话"}
            try:
                msg = await send_dm_message(
                    db, session["session_id"], sender_id=agent_user_id,
                    content=caption if caption else " ",
                    attachments=attachments,
                )
                await db.commit()
            except ValueError as e:
                await db.rollback()
                return {"error": True, "message": str(e)}
            except Exception as e:
                await db.rollback()
                logger.error(f"send_file DM 失败: {e}", exc_info=True)
                return {"error": True, "message": f"发送失败: {str(e)}"}

            # WebSocket 推送（对齐 send_dm.py 模式）
            if manager:
                await manager.broadcast_to_dm(
                    session["session_id"],
                    {"type": "message", "conversation_type": "dm", "data": {**msg, "sender_name": agent_name}},
                )

            return {
                "success": True,
                "message": f"发送了 {len(attachments)} 个文件到私信",
                "attachments": attachments,
            }

        else:
            # ── 群聊 ──
            try:
                message = await send_gm_message(
                    db, group_id=target_group,
                    sender_type="ai", sender_id=agent_user_id,
                    content=caption if caption else "",
                    attachments=attachments,
                )
                await db.commit()
            except Exception as e:
                await db.rollback()
                logger.error(f"send_file 群聊失败: {e}", exc_info=True)
                return {"error": True, "message": f"发送失败: {str(e)}"}

            # 广播
            msg_data = gm_message_to_dict(message, sender_name=agent_name, sender_avatar_url=sender_avatar)
            if manager:
                await manager.broadcast_to_group(target_group, {"type": "message", "data": msg_data})

            # 触发其他 AI
            from app.ai.response_worker import message_queue
            import asyncio
            next_depth = context.get("chain_depth", 0) + 1
            try:
                message_queue.put_nowait({
                    "group_id": target_group,
                    "message_id": message.id,
                    "content": caption,
                    "sender_type": "ai",
                    "sender_id": agent_user_id,
                    "chain_depth": next_depth,
                })
            except asyncio.QueueFull:
                pass

            return {
                "success": True,
                "message": f"发送了 {len(attachments)} 个文件到群聊{channel_note}",
                "attachments": attachments,
            }


ToolRegistry.register(SendFile)
