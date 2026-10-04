"""
send_friend_request 工具 — AI 向人类用户或其他 AI 发送好友申请
"""
import logging
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.tools.base import ToolPlugin, ToolRegistry

logger = logging.getLogger(__name__)


class SendFriendRequest(ToolPlugin):
    name = "send_friend_request"
    description = (
        "向指定用户或 AI 发送好友申请。"
        "发送后对方会收到通知，可以接受或拒绝。"
        "如果对方已经向你发送过申请，则双向自动接受成为好友。"
        "对方的 ID 是 users 表中的统一 ID（可从群聊消息格式「名字(ID:数字)」中获取，或通过搜索找到）。"
    )
    segment = "chat_social"
    parameters = {
        "target_id": {
            "type": "integer",
            "description": "对方的 users.id（统一 ID，人类和 AI 都在 users 表中）。",
        },
        "message": {
            "type": "string",
            "nullable": True,
            "description": "申请附言（可选，最多 200 字）。介绍你想加好友的原因。",
        },
    }
    required = ["target_id"]
    states = ["active"]
    admin_description = "发送好友申请。AI 想与用户或其他 AI 建立好友关系时调用，支持双向自动接受。"
    trigger_condition = "AI 想添加好友时"

    async def execute(self, db: AsyncSession, agent_id: int, group_id: int | None,
                      arguments: dict, context: dict) -> dict:
        from app.repositories.friend_repo import SQLAlchemyFriendRepository
        from app.services.social.friend_service import send_friend_request
        from app.models.agent import Agent as AgentModel

        target_id = arguments["target_id"]
        message = (arguments.get("message") or "").strip() or None

        # 获取 AI 的 user_id
        agent_result = await db.execute(select(AgentModel).where(AgentModel.id == agent_id))
        agent = agent_result.scalar_one_or_none()
        if agent is None:
            return {"error": True, "message": "AI 不存在"}
        if agent.user_id is None:
            return {"error": True, "message": "AI 尚未初始化统一 ID，请稍后再试"}

        # 自动检测目标类型：查 agents 表判断是否为 AI
        target_type = "human"
        try:
            target_agent = await db.execute(
                select(AgentModel).where(AgentModel.user_id == target_id)
            )
            if target_agent.scalar_one_or_none():
                target_type = "ai"
        except Exception:
            pass

        try:
            result = await send_friend_request(
                friend_repo=SQLAlchemyFriendRepository(db),
                requester_id=agent.user_id,
                target_type=target_type,
                target_id=target_id,
                message=message,
            )
            await db.commit()
        except ValueError as e:
            await db.rollback()
            return {"error": True, "message": str(e)}
        except Exception as e:
            await db.rollback()
            logger.error(f"send_friend_request 失败: {e}", exc_info=True)
            return {"error": True, "message": f"发送好友申请失败: {str(e)}"}

        # WebSocket 通知目标用户
        manager = context.get("manager")
        if manager:
            agent_name = context.get("agent_name", f"AI:{agent_id}")
            try:
                await manager.send_to_user(target_id, {
                    "type": "friend_notification",
                    "data": {
                        "event": "request_received",
                        "request_id": result.get("request_id"),
                        "requester_id": agent.user_id,
                        "requester_name": agent_name,
                        "target_type": target_type,
                        "target_id": target_id,
                        "message": message,
                        "status": result.get("status", "pending"),
                        "auto_respond": result.get("auto", False),
                    },
                })
            except Exception:
                logger.warning(f"好友申请 WebSocket 通知失败 (target={target_id})", exc_info=True)

        agent_name = context.get("agent_name", f"AI:{agent_id}")
        if result.get("auto"):
            return {
                "success": True,
                "auto_accepted": True,
                "message": f"你和对方互相发送了好友申请，已自动成为好友",
            }
        return {
            "success": True,
            "status": result.get("status", "pending"),
            "message": f"已向 ID:{target_id} 发送好友申请",
            "request_id": result.get("request_id"),
        }


ToolRegistry.register(SendFriendRequest)
