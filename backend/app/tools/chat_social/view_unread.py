"""
view_unread 工具 — AI 查看未读消息和所在群聊
"""
import logging
from sqlalchemy import select as sa_select
from sqlalchemy.ext.asyncio import AsyncSession
from app.tools.base import ToolPlugin, ToolRegistry

logger = logging.getLogger(__name__)


class ViewUnread(ToolPlugin):
    name = "view_unread"
    description = ("查看你所在的所有群聊及其未读消息，以及你还没处理的私信。"
                   "即使某个群没有未读消息，你也能看到它的存在。这样你就不会误以为自己不在任何群聊里。")
    segment = "chat_social"
    parameters = {}
    required = []
    states = ["active", "dnd"]
    admin_description = "查看未读消息和暂存消息。AI 唤醒或回归时查看错过的对话内容。"
    trigger_condition = "AI 回归或唤醒时"

    async def execute(self, db: AsyncSession, agent_id: int, group_id: int | None,
                      arguments: dict, context: dict) -> dict:
        from app.chat.delivery import check_unread, check_unread_dms
        from app.models.group import GroupMember, Group
        from app.models.agent import Agent as AgentModel

        # v2.0.0: agent_id 是 agent.id，但 group_members.member_id 统一为 user_id
        lookup_id = agent_id
        agent_result = await db.execute(
            sa_select(AgentModel).where(AgentModel.id == agent_id)
        )
        agent = agent_result.scalar_one_or_none()
        if agent and agent.user_id:
            lookup_id = agent.user_id

        member_result = await db.execute(
            sa_select(GroupMember).where(
                GroupMember.member_type == "ai",
                GroupMember.member_id == lookup_id,
            )
        )
        memberships = member_result.scalars().all()

        # 私信未读来自 dm_messages.read_at（单一真相），不额外记账
        dms = await check_unread_dms(db, agent_id)

        if not memberships:
            return {"groups": [], "dms": dms, "message": "你不在任何群聊中"}

        group_ids = [m.group_id for m in memberships]
        group_result = await db.execute(
            sa_select(Group).where(Group.id.in_(group_ids))
        )
        group_map = {g.id: g.name for g in group_result.scalars().all()}

        unread_summaries = await check_unread(db, agent_id)
        unread_map = {s["group_id"]: s for s in unread_summaries}

        groups = []
        for gid in group_ids:
            if gid in unread_map:
                groups.append(unread_map[gid])
            else:
                groups.append({
                    "group_id": gid,
                    "group_name": group_map.get(gid, f"群聊#{gid}"),
                    "unread_count": 0,
                    "last_message_preview": None,
                    "last_message_at": None,
                })

        groups.sort(key=lambda g: g.get("unread_count", 0), reverse=True)
        return {"groups": groups, "dms": dms}


ToolRegistry.register(ViewUnread)
