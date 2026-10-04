"""
handle_friend_request 工具 — AI 处理好友申请（有人申请加你时）

接受或拒绝一条好友申请。申请进账本时是一条「【好友申请】…」通知，里面有申请 id 与留言；
你决定通过与否后调用本工具。
"""
import logging
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.tools.base import ToolPlugin, ToolRegistry

logger = logging.getLogger(__name__)


class HandleFriendRequest(ToolPlugin):
    name = "handle_friend_request"
    description = (
        "处理好友申请（有人申请加你为好友时）：接受或拒绝。\n"
        "账本里「【好友申请】…」那条通知带着申请 id 和对方留言。\n"
        "通过 = 成为好友（对方会收到通知）；拒绝 = 拒绝对方。\n"
        "也可以选择暂不处理（申请保持待处理，通知一直在）。"
    )
    segment = "chat_social"
    parameters = {
        "action": {
            "type": "string", "enum": ["accept", "reject"],
            "description": "accept=通过好友申请；reject=拒绝",
        },
        "request_id": {
            "type": "integer",
            "description": "申请的 id（来自账本里「【好友申请】…」那条通知）",
        },
    }
    required = ["action", "request_id"]
    states = ["active", "dnd", "inactive"]
    admin_description = "AI 处理好友申请（接受/拒绝）"
    trigger_condition = "AI 收到好友申请、用户让 AI 处理申请时"

    async def execute(self, db: AsyncSession, agent_id: int, group_id: int | None,
                      arguments: dict, context: dict) -> dict:
        from app.models.agent import Agent as AgentModel
        from app.repositories.friend_repo import SQLAlchemyFriendRepository
        from app.services.social.friend_service import accept_friend_request, reject_friend_request

        action = str(arguments.get("action") or "")
        request_id = int(arguments.get("request_id") or 0)
        if action not in ("accept", "reject") or not request_id:
            return {"success": False, "error": "缺少 action(accept/reject) 或 request_id"}

        agent = (await db.execute(
            select(AgentModel).where(AgentModel.id == agent_id)
        )).scalar_one_or_none()
        if agent is None:
            return {"success": False, "error": "AI 不存在"}

        # 校验申请确实是发给自己的（防越权处理别人的申请）
        from app.models.friendship import FriendshipRequest
        req = (await db.execute(
            select(FriendshipRequest).where(FriendshipRequest.id == request_id)
        )).scalar_one_or_none()
        if req is None or req.target_type != "ai" or req.target_id != agent.user_id:
            return {"success": False, "error": "该申请不是发给你的，无法处理"}

        # 好友服务吃的是仓储（关键字参数），不是裸会话——借同一个 session 包装
        friend_repo = SQLAlchemyFriendRepository(db)
        if action == "accept":
            result = await accept_friend_request(
                friend_repo=friend_repo, request_id=request_id, user_id=agent.user_id,
            )
            await db.commit()
            logger.info(f"🤝 AI「{agent.name}」通过了好友申请 #{request_id}")
            return {"success": True, "result": "已通过好友申请", **result}
        else:
            result = await reject_friend_request(
                friend_repo=friend_repo, request_id=request_id, user_id=agent.user_id,
            )
            await db.commit()
            logger.info(f"🤝 AI「{agent.name}」拒绝了好友申请 #{request_id}")
            return {"success": True, "result": "已拒绝好友申请", **result}


ToolRegistry.register(HandleFriendRequest)
