"""
send_dm 工具 — AI 向好友发送私信
"""
import logging
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.tools.base import ToolPlugin, ToolRegistry

logger = logging.getLogger(__name__)


class SendDM(ToolPlugin):
    name = "send_dm"
    description = "向任何人发送私信。私信是一对一的，其他人看不到。发送后对方会立即收到通知。你需要知道对方的 user_id（可从群聊消息格式「名字(ID:数字)」中获取，或通过搜索找到）。"
    segment = "chat_social"
    parameters = {
        "target_user_id": {
            "type": "integer",
            "description": "对方的 users.id（统一 ID，人类和 AI 都在 users 表中）。可从群聊消息格式「名字(ID:数字)」中获取，或通过搜索找到。",
        },
        "content": {"type": "string", "description": "消息内容（支持 Markdown + 彩色文字）。彩色文字：标签语法 [gold]金色[/gold] [red]红色[/red] [blue]蓝色[/blue] [green]绿色[/green] [purple]紫色[/purple] [orange]橙色[/orange] [pink]粉色[/pink] [gray]灰色[/gray]；HTML 语法 <span class=\"text-red\">红色</span> 兼容（两种任选）"},
        "reply_to": {"type": "integer", "description": "（可选）要引用回复的那条消息的 msg_id。注意：消息末尾的 [msg_id=…] 是给你读的标记，回复某条消息请用这个参数，不要把它抄进正文。"},
    }
    required = ["target_user_id", "content"]
    states = ["active"]
    admin_description = "发送私信给指定用户。AI 需要私下沟通时调用，自动获取或创建 DM 会话，支持附件引用。"
    trigger_condition = "AI 需要私密沟通时"

    async def execute(self, db: AsyncSession, agent_id: int, group_id: int | None,
                      arguments: dict, context: dict) -> dict:
        from app.chat.dm import (
            get_or_create_dm_session, send_dm_message,
        )
        from app.models.agent import Agent as AgentModel

        target_user_id = arguments["target_user_id"]
        content = arguments["content"]
        reply_to = arguments.get("reply_to")

        agent_result = await db.execute(select(AgentModel).where(AgentModel.id == agent_id))
        agent = agent_result.scalar_one_or_none()
        if agent is None:
            return {"error": True, "message": "AI 不存在"}
        if agent.user_id is None:
            return {"error": True, "message": "AI 尚未初始化统一 ID，请稍后再试"}

        try:
            dm = await get_or_create_dm_session(
                db, current_user_id=agent.user_id, target_user_id=target_user_id,
            )
            session_id = dm["session_id"]
            msg = await send_dm_message(
                db, session_id, sender_id=agent.user_id, content=content, reply_to=reply_to,
            )
            # 2026-08-09: AI 发送私信后同样触发接收方 AI 回复（AI↔AI 限额由触发逻辑内部检查）
            from app.routers.dm import _maybe_trigger_dm_ai_reply
            await _maybe_trigger_dm_ai_reply(
                db, session_id, msg, agent.user_id,
                sender_name=context.get("agent_name", f"AI:{agent_id}"),
            )
            # 2026-08-09: 聊天即情景——发送方主动进入目标会话，切帧到目标会话（接收侧触发已建帧，
            # 主动发送侧也要建/切，否则 AI 换回其他会话时感知不到「我正在跟 X 聊」）
            try:
                from app.services.agent.state_stack_service import ensure_active_frame
                from app.models.user import User as UserModel
                trow = await db.execute(
                    select(UserModel.username).where(UserModel.id == target_user_id)
                )
                target_name = trow.scalar_one_or_none() or f"用户{target_user_id}"
                await ensure_active_frame(
                    db, agent.id, "dm", session_id,
                    title=target_name, actor_name=target_name,
                )
            except Exception as e:
                logger.warning(f"send_dm 发送方会话帧维护失败（非致命）: {e}")
            await db.commit()
        except ValueError as e:
            return {"error": True, "message": str(e)}

        # WebSocket 广播
        agent_name = context.get("agent_name", f"AI:{agent_id}")
        manager = context.get("manager")
        if manager:
            await manager.broadcast_to_dm(
                session_id,
                {"type": "message", "conversation_type": "dm", "data": {**msg, "sender_name": agent_name}},
            )

        return {
            "success": True,
            "session_id": session_id,
            "message_id": msg["id"],
            "content": content,
        }


ToolRegistry.register(SendDM)
