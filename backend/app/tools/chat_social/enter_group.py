"""
enter_group 工具 — AI 主动进入群聊，加载未读消息
"""
import logging
from sqlalchemy import select as sa_select
from sqlalchemy.ext.asyncio import AsyncSession
from app.tools.base import ToolPlugin, ToolRegistry
from app.services.agent.state_stack_service import make_state_frame, push_state

logger = logging.getLogger(__name__)


class EnterGroup(ToolPlugin):
    name = "enter_group"
    description = (
        "主动进入一个群聊，加载未读消息并切换上下文。\n"
        "调用后：1) 看到未读消息数量 2) 上下文切换到这个群 "
        "3) 之前的任务被保存为暂停状态。\n"
        "注意：这不会自动发送消息到群聊——你需要切换后自己查看消息并决定是否回复。"
    )
    segment = "chat_social"
    parameters = {
        "group_id": {"type": "integer", "description": "要进入的群聊 ID"},
        "reason": {"type": "string", "nullable": True, "description": "进入原因（可选）"},
    }
    required = ["group_id"]
    states = ["active", "dnd"]

    async def execute(self, db: AsyncSession, agent_id: int, group_id: int | None,
                      arguments: dict, context: dict) -> dict:
        from app.models.group import Group

        target_group = arguments["group_id"]
        reason = arguments.get("reason", "主动查看")

        # 验证成员资格（用 _get_member 兼容 agent.id/user_id 两种格式）
        from app.chat.gm import _get_member
        member = await _get_member(db, target_group, "ai", agent_id)
        if not member:
            return {"error": True, "message": f"你不在群聊 {target_group} 中"}

        # 群名
        group_result = await db.execute(sa_select(Group).where(Group.id == target_group))
        group = group_result.scalar_one_or_none()
        group_name = group.name if group else f"群聊#{target_group}"

        # 未读消息数
        unread_count = 0
        preview = ""
        from app.chat.delivery import check_unread
        try:
            unread_list = await check_unread(db, agent_id)
            unread_info = next((u for u in unread_list if u.get("group_id") == target_group), None)
            if unread_info:
                unread_count = unread_info.get("unread_count", 0)
                preview = unread_info.get("last_message_preview", "")
        except Exception:
            pass

        # 切到群帧（同群再进＝切回原帧，不叠第二帧）
        frame = make_state_frame(
            type_="group_chat",
            context_ref=f"group:{target_group}",
            why=reason,
            doing=f"进入群「{group_name}」查看 {unread_count} 条未读消息",
        )
        stack, _ = await push_state(db, agent_id, frame)
        await db.commit()

        return {
            "success": True,
            "group_id": target_group,
            "group_name": group_name,
            "unread_count": unread_count,
            "last_message_preview": preview[:100] if preview else "",
            "message": f"已进入群「{group_name}」，有 {unread_count} 条未读消息。如需查看消息请使用 get_gm_messages(group_id={target_group})",
            "stack_depth": len(stack),
        }


ToolRegistry.register(EnterGroup)
