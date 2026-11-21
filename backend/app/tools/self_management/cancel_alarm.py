"""
cancel_alarm 工具 — AI 取消闹钟
"""
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from app.tools.base import ToolPlugin, ToolRegistry

logger = logging.getLogger(__name__)


class CancelAlarm(ToolPlugin):
    name = "cancel_alarm"
    persistent_effect = True   # 写进持久状态：结果要进账本，否则下一个自己不知道写了什么
    description = "取消一个之前设定的闹钟。如果你改变主意了，或者任务已经不需要做了，可以用这个来取消。"
    segment = "self_management"
    parameters = {
        "alarm_id": {"type": "integer", "description": "要取消的闹钟 ID（从 list_alarms 可以查看你的所有闹钟）"},
    }
    required = ["alarm_id"]
    states = ["active", "dnd", "inactive"]
    admin_description = "取消已设置的闹钟。计划变更或任务不再需要时调用，释放闹钟资源。"
    trigger_condition = "计划变更或任务取消时"

    async def execute(self, db: AsyncSession, agent_id: int, group_id: int | None,
                      arguments: dict, context: dict) -> dict:
        from app.ai.alarm import cancel_alarm as svc_cancel_alarm

        alarm_id = arguments.get("alarm_id")
        if not alarm_id:
            return {"error": True, "message": "请提供 alarm_id"}

        try:
            result = await svc_cancel_alarm(db, agent_id, alarm_id=int(alarm_id))
            await db.commit()
            return result
        except Exception as e:
            logger.error(f"cancel_alarm 失败 (agent={agent_id}, alarm={alarm_id}): {e}", exc_info=True)
            return {"error": True, "message": f"取消闹钟失败: {e}"}


ToolRegistry.register(CancelAlarm)
