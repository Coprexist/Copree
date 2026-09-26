"""
cancel_dnd 工具 — 取消群聊免打扰
"""
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from app.tools.base import ToolPlugin, ToolRegistry

logger = logging.getLogger(__name__)


class CancelDND(ToolPlugin):
    name = "cancel_dnd"
    description = (
        "取消免打扰：默认取消**整个群**的免打扰，恢复接收该群的普通消息；"
        "带上 target_user_id 就只恢复接收那个人的消息（取消 silence_member 设的那种按人静音）；"
        "按人静音只对你自己单向生效，所以取消也只是你这边恢复。"
    )
    segment = "chat_social"
    parameters = {
        "group_id": {"type": "integer", "description": "要取消免打扰的群聊 ID"},
        "target_user_id": {
            "type": "integer", "nullable": True,
            "description": "（可选）只恢复接收这个人的消息（取消他的按人静音），不动整群免打扰",
        },
    }
    required = ["group_id"]
    states = ["active"]

    async def execute(self, db: AsyncSession, agent_id: int, group_id: int | None,
                      arguments: dict, context: dict) -> dict:
        from app.chat.delivery import cancel_group_dnd, cancel_member_silence

        target_group = arguments.get("group_id", group_id)
        target_user = arguments.get("target_user_id")
        if target_user is not None:
            await cancel_member_silence(db, agent_id, int(target_group), int(target_user))
            await db.commit()
            return {
                "success": True,
                "message": f"已恢复接收 {int(target_user)} 的消息（此前只是你单方面不接收他，他一直在正常说话）",
                "__note": f"恢复接收 {int(target_user)} 的消息",
            }
        await cancel_group_dnd(db, agent_id, target_group)
        await db.commit()
        return {"success": True, "message": f"已取消群聊 {target_group} 的免打扰"}


ToolRegistry.register(CancelDND)
