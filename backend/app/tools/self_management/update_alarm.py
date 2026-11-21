"""
update_alarm 工具 — AI 修改闹钟
"""
import logging
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from app.tools.base import ToolPlugin, ToolRegistry

logger = logging.getLogger(__name__)


class UpdateAlarm(ToolPlugin):
    name = "update_alarm"
    persistent_effect = True   # 写进持久状态：结果要进账本，否则下一个自己不知道写了什么
    description = "修改一个闹钟的唤醒时间或任务内容。设错了时间？要调整任务？用这个改。wake_at 和 task 可以只改一个。"
    segment = "self_management"
    parameters = {
        "alarm_id": {"type": "integer", "description": "要修改的闹钟 ID"},
        "wake_at": {"type": "string", "nullable": True, "description": "新的唤醒时间（ISO 8601 格式）。不传则不修改。"},
        "task": {"type": "string", "nullable": True, "description": "新的任务描述。不传则不修改。"},
    }
    required = ["alarm_id"]
    states = ["active", "dnd", "inactive"]
    admin_description = "修改已有闹钟的唤醒时间或原因。调整计划时调用，自动重新调度闹钟。"
    trigger_condition = "需要调整已有定时任务时"

    async def execute(self, db: AsyncSession, agent_id: int, group_id: int | None,
                      arguments: dict, context: dict) -> dict:
        from app.ai.alarm import update_alarm as svc_update_alarm

        alarm_id = arguments.get("alarm_id")
        if not alarm_id:
            return {"error": True, "message": "请提供 alarm_id"}

        wake_at_str = arguments.get("wake_at")
        task = arguments.get("task")

        wake_at = None
        if wake_at_str:
            try:
                wake_at = datetime.fromisoformat(wake_at_str)
                if wake_at.tzinfo is None:
                    wake_at = wake_at.replace(tzinfo=timezone.utc)
                if wake_at <= datetime.now(timezone.utc):
                    return {"error": True, "message": "唤醒时间不能是过去"}
            except ValueError as e:
                return {"error": True, "message": f"wake_at 格式无效: {e}"}

        try:
            result = await svc_update_alarm(db, agent_id, alarm_id=int(alarm_id), wake_at=wake_at, task=task)
            await db.commit()
            return result
        except Exception as e:
            logger.error(f"update_alarm 失败 (agent={agent_id}, alarm={alarm_id}): {e}", exc_info=True)
            return {"error": True, "message": f"修改闹钟失败: {e}"}


ToolRegistry.register(UpdateAlarm)
