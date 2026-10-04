"""
list_friends 工具 — AI 查看自己的好友列表

加好友前先确认「对方是不是早就是我的好友」，比发一次申请撞上
「已经是好友了」省一轮；也用来回顾自己加了谁。
"""
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from app.tools.base import ToolPlugin, ToolRegistry

logger = logging.getLogger(__name__)

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


class ListFriends(ToolPlugin):
    name = "list_friends"
    description = (
        "列出你的好友（人类与其他 AI），含对方的统一 ID、名字、是否特别关心。\n"
        "加好友前想确认对方是不是已经是你的好友，先查这里。"
    )
    segment = "chat_social"
    parameters = {
        "limit": {
            "type": "integer",
            "nullable": True,
            "description": f"最多返回多少条（默认 {DEFAULT_LIMIT}，上限 {MAX_LIMIT}）",
        },
    }
    required = []
    states = ["active", "dnd"]
    admin_description = "查看 AI 自己的好友列表"
    trigger_condition = "AI 想确认自己有哪些好友、或确认某人是否已经是好友时"

    async def execute(self, db: AsyncSession, agent_id: int, group_id: int | None,
                      arguments: dict, context: dict) -> dict:
        from app.models.agent import Agent as AgentModel
        from app.repositories.friend_repo import SQLAlchemyFriendRepository
        from app.services.social.friend_service import list_friends

        agent = await db.get(AgentModel, agent_id)
        if agent is None:
            return {"error": True, "message": "AI 不存在"}
        if not agent.user_id:
            return {"error": True, "message": "AI 尚未初始化统一 ID，请稍后再试"}

        try:
            limit = int(arguments.get("limit") or DEFAULT_LIMIT)
        except (TypeError, ValueError):
            limit = DEFAULT_LIMIT
        limit = max(1, min(limit, MAX_LIMIT))

        rows = await list_friends(
            friend_repo=SQLAlchemyFriendRepository(db), user_id=agent.user_id, limit=limit,
        )
        friends = []
        for r in rows:
            item = {
                "id": r["friend_user_id"],      # users.id：私信、加好友都用它
                "name": r["friend_name"],
                "type": r["friend_type"],
                "is_priority": r["is_priority"],
            }
            if r["state"]:
                item["state"] = r["state"]      # 只有 AI 好友有
            friends.append(item)
        return {"count": len(friends), "friends": friends}


ToolRegistry.register(ListFriends)
