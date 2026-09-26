"""
set_dnd 工具 — AI 设置群聊免打扰
"""
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from app.tools.base import ToolPlugin, ToolRegistry

logger = logging.getLogger(__name__)


class SetDND(ToolPlugin):
    name = "set_dnd"
    description = (
        "设置群聊免打扰状态。免打扰期间你不会被该群的普通消息触发，"
        "但以下情况仍会穿透免打扰并唤醒你：@提及你的消息、@all/@everyone/@全体、群公告、特别关心的人。"
        "要连这些也不被叫（连 @ 都不唤醒）那是**屏蔽**，不是免打扰——用 mute_group（≤30 分钟）。"
        "只不接收某一个人的消息（连他的 @ 也不理、他照常说话）用 silence_member。"
        "免打扰期间消息一律暂存，等你自己 enter_group / view_unread 去看。"
        "你可以稍后通过 enter_group 主动进入查看错过的消息，或调用 cancel_dnd 取消免打扰。"
    )
    segment = "chat_social"
    parameters = {
        "group_id": {"type": "integer", "description": "目标群聊 ID"},
        "duration_minutes": {
            "type": "integer", "nullable": True,
            "description": "免打扰时长（分钟），null 表示永久免打扰",
        },
    }
    required = ["group_id"]
    states = ["active"]
    admin_description = "设置免打扰模式。AI 需要专注时阻止消息推送，收到的消息会暂存待后续查看。"
    trigger_condition = "AI 主动进入专注状态时"

    async def execute(self, db: AsyncSession, agent_id: int, group_id: int | None,
                      arguments: dict, context: dict) -> dict:
        from app.chat.delivery import set_group_dnd

        target_group = arguments.get("group_id", group_id)
        duration = arguments.get("duration_minutes")

        await set_group_dnd(db, agent_id, target_group, duration)
        await db.commit()

        if duration:
            return {"success": True, "message": f"已设置免打扰 {duration} 分钟"}
        return {"success": True, "message": "已设置永久免打扰"}


ToolRegistry.register(SetDND)
