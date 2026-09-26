"""
mute_group 工具 — AI 设置群聊屏蔽（比免打扰更强，连 @/公告都不接收）
"""
import logging
from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from app.tools.base import ToolPlugin, ToolRegistry
from app.utils.pure.timeutil import utc_now

logger = logging.getLogger(__name__)


class MuteGroup(ToolPlugin):
    name = "mute_group"
    description = (
        "设置群聊屏蔽——比免打扰更强的静默模式。屏蔽期间你**完全不受打扰**："
        "连 @提及、@all、群公告都无法穿透唤醒你。"
        "适用于极度不想被打扰的场景（如深度工作、休息等）。"
        "时长上限 30 分钟，到期自动恢复。可以用 cancel_dnd 提前取消。"
    )
    segment = "chat_social"
    parameters = {
        "group_id": {"type": "integer", "description": "目标群聊 ID"},
        "duration_minutes": {
            "type": "integer",
            "description": "屏蔽时长（分钟），最大 30 分钟",
        },
    }
    required = ["group_id", "duration_minutes"]
    states = ["active", "dnd"]
    admin_description = "群聊屏蔽——比免打扰更强，@和公告也无法穿透。AI 需要完全不受打扰时使用。"
    trigger_condition = "AI 需要完全不受打扰时"

    async def execute(self, db: AsyncSession, agent_id: int, group_id: int | None,
                      arguments: dict, context: dict) -> dict:
        from app.chat.gm import _get_member
        from app.models.agent import Agent as AgentModel
        from sqlalchemy import select

        target_group = arguments.get("group_id", group_id)
        duration = min(int(arguments["duration_minutes"]), 30)

        # 定位群成员记录
        agent_result = await db.execute(
            select(AgentModel).where(AgentModel.id == agent_id)
        )
        agent = agent_result.scalar_one_or_none()
        if not agent:
            return {"error": True, "message": "AI 不存在"}
        lookup_id = agent.user_id or agent_id

        from app.models.group import GroupMember
        from sqlalchemy import and_
        member_result = await db.execute(
            select(GroupMember).where(
                and_(
                    GroupMember.group_id == target_group,
                    GroupMember.member_type == "ai",
                    GroupMember.member_id == lookup_id,
                )
            )
        )
        member = member_result.scalar_one_or_none()
        if not member:
            return {"error": True, "message": f"你不是群 {target_group} 的成员"}

        # 设置屏蔽截止时间
        member.muted_until = utc_now() + timedelta(minutes=duration)
        await db.commit()

        return {
            "success": True,
            "message": (
                f"已屏蔽群 {target_group} {duration} 分钟。"
                "期间任何消息（含 @/公告）都不会触发你。到期自动恢复。"
            ),
        }


ToolRegistry.register(MuteGroup)
