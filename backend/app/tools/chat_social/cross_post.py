"""
cross_post 工具 — AI 跨对话发消息（群↔群、群↔私信、私信↔私信）
"""
import logging
from sqlalchemy import select as sa_select
from sqlalchemy.ext.asyncio import AsyncSession
from app.tools.base import ToolPlugin, ToolRegistry

logger = logging.getLogger(__name__)


class CrossPost(ToolPlugin):
    name = "cross_post"
    description = "跨对话发消息——把你在一个群聊/私信里知道的信息，带到另一个群聊/私信里去。你的记忆是跨对话共享的，这个工具让你能主动传递信息。使用场景：群A讨论了一个结论，你觉得群B也需要知道→ cross_post(source_type='group', source_id=群A的ID, target_type='group', target_id=群B的ID, content='之前在群A我们讨论过…')。也可在群聊和私信之间互相传递。"
    segment = "chat_social"
    parameters = {
        "source_type": {
            "type": "string", "enum": ["group", "dm"],
            "description": "来源对话类型：group（群聊）或 dm（私信）",
        },
        "source_id": {
            "type": "integer",
            "description": "来源对话 ID（group_id 或 session 内部 id）",
        },
        "target_type": {
            "type": "string", "enum": ["group", "dm"],
            "description": "目标对话类型：group（群聊）或 dm（私信）",
        },
        "target_id": {
            "type": "integer",
            "description": "目标对话 ID（group_id 或 dm_sessions.id）",
        },
        "content": {
            "type": "string",
            "description": "要在目标对话中发送的内容。系统会自动加上「跨群引用」标记和来源名称。",
        },
    }
    required = ["source_type", "source_id", "target_type", "target_id", "content"]
    states = ["active", "dnd"]
    admin_description = "跨群转发消息到其他群聊。AI 判断某条消息对其他群有价值时自动调用。"
    trigger_condition = "消息对其他群有价值时"

    async def execute(self, db: AsyncSession, agent_id: int, group_id: int | None,
                      arguments: dict, context: dict) -> dict:
        from app.models.agent import Agent as AgentModel

        source_type = arguments["source_type"]
        source_id = arguments["source_id"]
        target_type = arguments["target_type"]
        target_id = arguments["target_id"]
        content = arguments["content"]

        agent_result = await db.execute(sa_select(AgentModel).where(AgentModel.id == agent_id))
        agent = agent_result.scalar_one_or_none()
        if agent is None:
            return {"error": True, "message": "AI 不存在"}
        if agent.user_id is None:
            return {"error": True, "message": "AI 尚未初始化统一 ID"}

        source_name = f"#{source_id}"
        try:
            if source_type == "group":
                from app.models.group import Group as GroupModel
                g_result = await db.execute(sa_select(GroupModel).where(GroupModel.id == source_id))
                g = g_result.scalar_one_or_none()
                if g:
                    source_name = g.name
            else:
                from app.models.dm import DMSession
                dm_result = await db.execute(sa_select(DMSession).where(DMSession.id == source_id))
                dm = dm_result.scalar_one_or_none()
                if dm:
                    source_name = f"私信会话#{dm.session_id}"
        except Exception:
            pass

        full_content = f"📢 **跨对话引用**（来自「{source_name}」）\n\n{content}"

        try:
            if target_type == "group":
                from app.chat.gm import send_gm_message, gm_message_to_dict, is_group_member
                if not await is_group_member(db, target_id, "ai", agent.user_id):
                    return {"error": True, "message": f"你不是群 {target_id} 的成员，无法发消息"}
                message = await send_gm_message(
                    db, group_id=target_id, sender_type="ai",
                    sender_id=agent.user_id, content=full_content,
                )
                await db.flush()
                manager = context.get("manager")
                if manager:
                    msg_data = gm_message_to_dict(message, sender_name=agent.name, sender_avatar_url=agent.avatar_url)
                    await manager.broadcast_to_group(
                        target_id, {"type": "message", "data": msg_data},
                    )
                return {"success": True, "message_id": message.id, "target_type": "group", "target_id": target_id}

            else:
                from app.chat.dm import get_or_create_dm_session, send_dm_message
                from app.models.dm import DMSession
                dm_result = await db.execute(sa_select(DMSession).where(DMSession.id == target_id))
                dm_session = dm_result.scalar_one_or_none()
                if dm_session is None:
                    return {"error": True, "message": f"私信会话 #{target_id} 不存在"}

                if agent.user_id not in (dm_session.user1_id, dm_session.user2_id):
                    return {"error": True, "message": "你不是这个私信会话的参与者，无法发消息"}

                msg = await send_dm_message(
                    db, dm_session.session_id,
                    sender_id=agent.user_id, content=full_content,
                )
                await db.flush()
                manager = context.get("manager")
                if manager:
                    await manager.broadcast_to_dm(
                        dm_session.session_id,
                        {"type": "message", "conversation_type": "dm",
                         "data": {**msg, "sender_name": agent.name}},
                    )
                return {"success": True, "message_id": msg["id"], "target_type": "dm",
                        "session_id": dm_session.session_id}

        except ValueError as e:
            return {"error": True, "message": str(e)}
        except Exception as e:
            logger.error(f"cross_post 失败 (agent={agent_id}): {e}", exc_info=True)
            return {"error": True, "message": f"跨对话发消息失败: {e}"}


ToolRegistry.register(CrossPost)
