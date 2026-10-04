"""
search_users 工具 — AI 按用户名搜索用户或 AI，获取其 ID 以进一步发起好友申请

搜索本身走 services/social/search_service.search_entities（站内搜索接口同一实现），
这里只把结果裁成对话里够用的几栏——头像、状态那些 AI 用不上，白占 token。
"""
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from app.tools.base import ToolPlugin, ToolRegistry

logger = logging.getLogger(__name__)


class SearchUsers(ToolPlugin):
    name = "search_users"
    description = (
        "按用户名或 AI 名搜索其他用户。返回匹配的列表，含 ID、名字和 is_friend"
        "（true 表示已经是你的好友，不要再发好友申请）。"
        "你可以通过此工具找到某人的 ID，然后用 send_friend_request 添加好友。"
        "支持模糊搜索，输入部分名称即可。"
    )
    segment = "chat_social"
    parameters = {
        "query": {
            "type": "string",
            "description": "搜索关键词（按用户名/AI 名模糊匹配，支持部分名称）",
        },
    }
    required = ["query"]
    states = ["active", "dnd"]
    admin_description = "按用户名搜索用户或 AI，获取 ID 用于加好友等操作。"
    trigger_condition = "AI 想搜索/查找某个人时"

    async def execute(self, db: AsyncSession, agent_id: int, group_id: int | None,
                      arguments: dict, context: dict) -> dict:
        from app.models.agent import Agent as AgentModel
        from app.repositories.search_repo import SQLAlchemySearchRepository
        from app.services.social.search_service import search_entities

        query = (arguments.get("query") or "").strip()
        if len(query) < 1:
            return {"users": [], "hint": "请提供至少一个字符的搜索关键词"}

        agent = await db.get(AgentModel, agent_id)
        if agent is None or not agent.user_id:
            return {"users": [], "hint": "AI 尚未初始化统一 ID，请稍后再试"}

        results = await search_entities(
            SQLAlchemySearchRepository(db), query, current_user_id=agent.user_id, limit=20,
        )
        users = []
        for r in results:
            item = {"id": r["id"], "name": r["name"], "type": r["type"], "is_friend": r["is_friend"]}
            if r["type"] == "ai" and r["owner_name"]:
                item["owner_name"] = r["owner_name"]  # 同名 AI 不少，制作者能帮着辨认
            users.append(item)
        return {"users": users}


ToolRegistry.register(SearchUsers)
