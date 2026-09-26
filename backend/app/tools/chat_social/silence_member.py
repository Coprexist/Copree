"""
silence_member 工具 — AI 按人静音（只对某个人：连 @ 也不唤醒）
"""
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from app.tools.base import ToolPlugin, ToolRegistry

logger = logging.getLogger(__name__)


class SilenceMember(ToolPlugin):
    name = "silence_member"
    description = (
        "把某个人静音一段时间：他在这群里说话（**哪怕 @ 你**）都不唤醒你，"
        "消息照样进群、你之后能翻到，只是不打断你。"
        "可以只按时长（duration_minutes 分钟内）、只按条数（message_count 他再说几条内），"
        "两个都给就谁先到算谁；都不给 = 永久静音。"
        "取消用 cancel_dnd 带上同一个 target_user_id。"
        "整群安静用 set_dnd（@ 仍会穿透）或 mute_group（≤30 分钟、连 @ 也不穿透）。"
    )
    segment = "chat_social"
    parameters = {
        "target_user_id": {
            "type": "integer",
            "description": "要静音的人（消息里说话人后面那个 id）",
        },
        "group_id": {
            "type": "integer", "nullable": True,
            "description": "群聊 ID，默认当前所在的群",
        },
        "duration_minutes": {
            "type": "integer", "nullable": True,
            "description": "静音多少分钟（不给 = 时间这一维不限制）",
        },
        "message_count": {
            "type": "integer", "nullable": True,
            "description": "他再说多少条内都别叫我（不给 = 条数这一维不限制）",
        },
    }
    required = ["target_user_id"]
    states = ["active"]
    admin_description = (
        "按人静音：对某个特定的人（连 @ 也不唤醒），可按分钟或按消息条数；"
        "适用于「这人太吵，先别理他」的场景。"
    )
    trigger_condition = "AI 想忽略某个特定的人一段时间时"

    async def execute(self, db: AsyncSession, agent_id: int, group_id: int | None,
                      arguments: dict, context: dict) -> dict:
        from app.chat.delivery import silence_member

        target_group = arguments.get("group_id", group_id)
        if not target_group:
            return {"error": True, "message": "没有群聊上下文：请给 group_id"}
        target_user = int(arguments["target_user_id"])
        duration = arguments.get("duration_minutes")
        count = arguments.get("message_count")

        await silence_member(
            db, agent_id, int(target_group), target_user,
            duration_minutes=int(duration) if duration else None,
            message_count=int(count) if count else None,
        )
        await db.commit()

        parts = []
        if duration:
            parts.append(f"{int(duration)} 分钟")
        if count:
            parts.append(f"他再说 {int(count)} 条")
        return {
            "success": True,
            "message": f"已静音 {target_user}：" + ("、".join(parts) if parts else "永久"),
        }


ToolRegistry.register(SilenceMember)
