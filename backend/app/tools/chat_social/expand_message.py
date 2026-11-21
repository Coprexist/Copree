"""
expand_message 工具 — AI 展开被折叠的消息完整内容
"""
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from app.tools.base import ToolPlugin, ToolRegistry

logger = logging.getLogger(__name__)


class ExpandMessage(ToolPlugin):
    name = "expand_message"
    description = (
        "展开被折叠的群聊消息完整内容。群聊消息超过一定长度会被折成"
        "「前 75% + …（中间省略 N 字）… + 后 25%」并带 [展开 id=N] 标记。"
        "**在引用、反驳或否认自己/别人说过某句话之前，只要看到这个标记就必须先展开核对**"
        "（只看折叠后的正文很容易漏掉中间那段，而那段往往就是要紧的结论）。"
        "可一次展开多条。消息带附件时会连文件名一起展开（文本文件还会附上前 1500 字内容）。"
        "展开的内容不计入上下文限制，会追加在当前消息末尾。"
    )
    segment = "chat_social"
    parameters = {
        "group_id": {"type": "integer", "description": "目标群聊 ID"},
        "msg_ids": {
            "type": "array",
            "items": {"type": "integer"},
            "description": "要展开的消息 ID 列表",
        },
    }
    required = ["group_id", "msg_ids"]
    states = ["active", "dnd"]

    async def execute(self, db: AsyncSession, agent_id: int, group_id: int | None,
                      arguments: dict, context: dict) -> dict:
        from app.models.message import Message
        from sqlalchemy import select

        target_group = arguments.get("group_id", group_id)
        msg_ids = arguments["msg_ids"]

        if not msg_ids:
            return {"error": True, "message": "请指定要展开的消息 ID"}

        from app.config import settings
        from app.utils.multimodal import attachment_marks, text_attachment_blocks

        result = await db.execute(
            select(Message.id, Message.content, Message.attachments)
            .where(Message.id.in_(msg_ids), Message.group_id == target_group)
        )
        rows = {r[0]: r for r in result.all()}

        from app.utils.message_serializer import mention_names
        from app.utils.text import render_mention_with_names

        mentioned = await mention_names(db, [row[1] or "" for row in rows.values()])
        expanded = {}
        inlined = 0
        for mid in msg_ids:
            row = rows.get(mid)
            if not row:
                expanded[str(mid)] = f"[消息 {mid} 不存在或不属于此群]"
                continue
            content = render_mention_with_names(row[1] or "", mentioned)
            marks = attachment_marks(row[2], said=content)
            if marks:
                content = f"{marks} {content}" if content else marks
            # 文本附件在这里才读内容：一次最多两份，展开多条时不至于把回复撑爆
            if inlined < 2:
                block = text_attachment_blocks(row[2], settings.data_dir, max_chars=1500)
                if block:
                    content = f"{content}\n\n{block}" if content else block
                    inlined += 1
            expanded[str(mid)] = content

        return {"success": True, "expanded": expanded}


ToolRegistry.register(ExpandMessage)
