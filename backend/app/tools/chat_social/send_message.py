"""
send_gm（群聊消息）工具 — AI 在群聊中发送消息
"""
import asyncio
import logging
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.tools.base import ToolPlugin, ToolRegistry

logger = logging.getLogger(__name__)


class SendGm(ToolPlugin):
    name = "send_gm"
    description = "在群聊中发送消息（Group Message）。仅用于群聊！私信请用 send_dm。要 @ 谁就在正文里写 <@!对方的id>（id 见每条消息说话人后面的「（id=N）」，人和 AI 都能 @），被 @ 的 AI 一定会注意到你的消息。@all 或 @ai 可以通知所有 AI。"
    segment = "chat_social"
    parameters = {
        "group_id": {"type": "integer", "description": "目标群聊 ID"},
        "content": {"type": "string", "description": "消息内容（支持 Markdown + 彩色文字）。彩色文字：标签语法 [gold]金色[/gold] [red]红色[/red] [blue]蓝色[/blue] [green]绿色[/green] [purple]紫色[/purple] [orange]橙色[/orange] [pink]粉色[/pink] [gray]灰色[/gray]；HTML 语法 <span class=\"text-red\">红色</span> 兼容（两种任选）"},
        "reply_to": {"type": "integer", "nullable": True, "description": "要引用回复的那条消息的 ID（可选）。注意：消息末尾的 [msg_id=…] 是给你读的标记，回复某条消息请用这个参数，不要把它抄进正文。"},
    }
    required = ["group_id", "content"]
    states = ["active"]
    admin_description = "在群聊中发送消息。AI 决定回复、主动发言或回应@时自动调用。消息内容支持文字和附件引用。"
    trigger_condition = "AI 决定回复或主动发言时"

    async def execute(self, db: AsyncSession, agent_id: int, group_id: int | None,
                      arguments: dict, context: dict) -> dict:
        from app.chat.gm import send_gm_message, gm_message_to_dict
        from app.models.agent import Agent as AgentModel

        # 统一用 user_id 作为 sender_id（v2.0.0 迁移后所有消息 sender_id 均为 users.id）
        a_obj = (await db.execute(select(AgentModel).where(AgentModel.id == agent_id))).scalar_one_or_none()
        user_id = a_obj.user_id if a_obj and a_obj.user_id else agent_id

        target_group = arguments.get("group_id", group_id)
        content = arguments["content"]
        reply_to = arguments.get("reply_to")

        message = await send_gm_message(
            db, group_id=target_group, sender_type="ai",
            sender_id=user_id, content=content, reply_to=reply_to,
        )
        await db.commit()

        # WebSocket 广播
        agent_name = context.get("agent_name", f"AI:{agent_id}")
        sender_avatar = getattr(a_obj, 'avatar_url', None) if a_obj else None
        msg_data = gm_message_to_dict(message, sender_name=agent_name, sender_avatar_url=sender_avatar)
        manager = context.get("manager")
        if manager:
            await manager.broadcast_to_group(
                target_group,
                {"type": "message", "data": msg_data},
            )

        # 推入消息队列，触发其他 AI 回复
        from app.ai.response_worker import message_queue
        next_depth = context.get("chain_depth", 0) + 1
        try:
            message_queue.put_nowait({
                "group_id": target_group,
                "message_id": message.id,
                "content": content,
                "sender_type": "ai",
                "sender_id": user_id,  # v2.0.0: 统一用 user_id
                "chain_depth": next_depth,
            })
        except asyncio.QueueFull:
            logger.warning("AI 回复队列已满，无法触发其他 AI 回复")

        # 自动提取关键信息存储为记忆
        try:
            from app.services.memory.memory_service import auto_extract_key_facts
            await auto_extract_key_facts(
                db, agent_id, target_group, content,
                api_base_url=context.get("api_base_url", "https://api.deepseek.com"),
                api_key=context.get("api_key"),
            )
        except Exception:
            pass

        # 记录消息吞吐量
        try:
            from app.services.infrastructure.metrics_collector import metrics
            await metrics.record_message(agent_id)
        except Exception:
            pass

        return {"success": True, "message_id": message.id}


ToolRegistry.register(SendGm)
