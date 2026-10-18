"""
update_ruler_time 工具 — AI 调整自己的尺时间。

尺时间决定「两条消息间隔多久才算同一条聊天链」，是 AI 判断要不要重新插话的唯一依据
（protocol 里写的是 120 秒默认值）。这条工具就是那句话的后端：不给它，AI 永远只能接受默认值。
"""
import logging
from sqlalchemy.ext.asyncio import AsyncSession

from app.tools.base import ToolPlugin, ToolRegistry

logger = logging.getLogger(__name__)

MIN_RULER = 10
MAX_RULER = 3600


class UpdateRulerTime(ToolPlugin):
    name = "update_ruler_time"
    description = (
        "调整你在本群的尺时间：消息间隔小于尺时间算同一条聊天链，大于等于才算新链（默认 120 秒）。"
        "想更快重新参与调小（最短 10 秒），想在这个话题里安静久一点调大。"
        "只影响你自己，不动别的 AI；只在群聊里有意义。"
    )
    segment = "chat_social"
    parameters = {
        "group_id": {"type": "integer", "description": "目标群聊 ID（默认当前群，跨群时才需要指定）"},
        "seconds": {"type": "integer", "description": "尺时间（秒），10-3600"},
    }
    required = ["seconds"]
    states = ["active", "dnd"]
    admin_description = "AI 调整自己的聊天链尺时间（默认 120 秒，范围 10-3600）。"

    async def execute(self, db: AsyncSession, agent_id: int, group_id: int | None,
                      arguments: dict, context: dict) -> dict:
        from app.ai.chat_chain import chat_chain_manager

        target = arguments.get("group_id") or group_id
        if not target:
            return {"success": False, "error": "尺时间只对群聊有效：私信里没有聊天链"}
        try:
            seconds = int(arguments.get("seconds"))
        except (TypeError, ValueError):
            return {"success": False, "error": "seconds 要给整数秒"}
        seconds = max(MIN_RULER, min(MAX_RULER, seconds))

        if not chat_chain_manager.update_ruler_time(agent_id, int(target), seconds):
            return {
                "success": False,
                "error": "你在这个群还没接过话，尺时间要等你第一次回复后才有意义",
            }
        return {"success": True, "message": f"你在群 {target} 的尺时间已设为 {seconds} 秒"}


ToolRegistry.register(UpdateRulerTime)
