"""
set_alarm 工具 — AI 给自己设定闹钟
"""
import logging
from datetime import datetime, timezone, timedelta
from sqlalchemy import select as _select, func as _func
from sqlalchemy.ext.asyncio import AsyncSession
from app.tools.base import ToolPlugin, ToolRegistry

logger = logging.getLogger(__name__)


class SetAlarm(ToolPlugin):
    name = "set_alarm"
    description = "给自己设定一个闹钟。到时间后你会被自动唤醒，系统会告诉你「你的闹钟响了」以及你当初设定要做什么事，然后你就可以执行那个任务了。可以用来：延迟回复（「5分钟后提醒我回复刚刚的话题」）、定时任务（「明天早上9点叫我整理本周聊天记录」）、短暂离开（「3分钟后叫醒我继续」）。delay_seconds 和 wake_at 二选一：用 delay_seconds 表示「多久之后」，用 wake_at 表示「具体时间点」。"
    segment = "self_management"
    parameters = {
        "task": {"type": "string", "description": "唤醒后要做什么事。写清楚，这样闹钟响时你才知道自己当时为什么要设这个闹钟。"},
        "delay_seconds": {"type": "integer", "nullable": True, "description": "多少秒后唤醒（相对时间）。例如：300 = 5分钟后，3600 = 1小时后。和 wake_at 二选一。"},
        "wake_at": {"type": "string", "nullable": True, "description": "具体唤醒时间，ISO 8601 格式（如 '2026-06-18T15:30:00+08:00'）。和 delay_seconds 二选一。"},
    }
    required = ["task"]
    states = ["active", "dnd", "inactive"]
    admin_description = "设置定时闹钟。到点自动唤醒 AI 执行指定任务，是 AI「心跳机制」和自主生命节律的基础。"
    trigger_condition = "AI 需要定时行动或计划未来任务时"

    async def execute(self, db: AsyncSession, agent_id: int, group_id: int | None,
                      arguments: dict, context: dict) -> dict:
        from app.ai.alarm import set_alarm as svc_set_alarm

        task = arguments.get("task", "").strip()
        if not task:
            return {"error": True, "message": "task 不能为空——请写清楚唤醒后要做什么"}

        delay_seconds = arguments.get("delay_seconds")
        wake_at_str = arguments.get("wake_at")

        now = datetime.now(timezone.utc)

        if delay_seconds is not None:
            if delay_seconds < 1:
                return {"error": True, "message": "delay_seconds 必须 ≥ 1 秒"}
            if delay_seconds > 30 * 86400:
                return {"error": True, "message": "delay_seconds 最长 30 天（2592000 秒）"}
            wake_at = now + timedelta(seconds=delay_seconds)
        elif wake_at_str:
            try:
                wake_at = datetime.fromisoformat(wake_at_str)
            except ValueError as e:
                return {"error": True, "message": f"wake_at 格式无效: {e}。请使用 ISO 8601 格式"}
            if wake_at <= now:
                return {"error": True, "message": "唤醒时间不能是过去。请设一个未来的时间。"}
            if (wake_at - now).total_seconds() > 30 * 86400:
                return {"error": True, "message": "闹钟最长只能设 30 天以后"}
        else:
            return {"error": True, "message": "请提供 delay_seconds（多少秒后）或 wake_at（具体时间），二选一"}

        if wake_at.tzinfo is None:
            wake_at = wake_at.replace(tzinfo=timezone.utc)

        # 活跃闹钟数量上限。活跃口径 = status="pending"，与 alarm 服务一致
        # （该列取值只有 pending / fired / cancelled，不存在 "active"）。
        # 此前该检查被 try/except 静默捕获，且导入的模块名不存在，上限校验始终未生效。
        from app.models.agent import Agent as AgentModel
        from app.models.alarm import AgentAlarm

        agent_row = (await db.execute(
            _select(AgentModel).where(AgentModel.id == agent_id)
        )).scalar_one_or_none()
        if agent_row:
            active_count = (await db.execute(
                _select(_func.count(AgentAlarm.id)).where(
                    AgentAlarm.agent_id == agent_id,
                    AgentAlarm.status == "pending",
                )
            )).scalar() or 0
            if active_count >= agent_row.max_alarms:
                return {"error": True, "message": f"活跃闹钟已达上限（{agent_row.max_alarms} 个），请先取消或等旧闹钟触发后再设新的"}

        try:
            result = await svc_set_alarm(db, agent_id, wake_at=wake_at, task=task)
            await db.commit()
            return {"success": True, **result}
        except Exception as e:
            logger.error(f"set_alarm 失败 (agent={agent_id}): {e}", exc_info=True)
            return {"error": True, "message": f"设定闹钟失败: {e}"}


ToolRegistry.register(SetAlarm)
