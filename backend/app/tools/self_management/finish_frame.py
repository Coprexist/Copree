"""finish_frame 工具 —— 状态帧后事交接完毕，销掉这帧

为什么需要他表态：帧从"用完即丢"改成"全量保留"之后，删除点必须唯一且明确。
平台猜不出"这帧的事他办完了没有"，猜错就是静默丢状态；所以由他调这个工具表态，
平台随后删帧，并往当前会话的历史里补一条「已处置」（只写一次，之后每轮吃缓存）。

和 close_state 的分工：close_state 是"关掉正在跑的状态"（帧留在存储里）；
finish_frame 是"这帧已经不在跑、后事我也办完了"（销掉记录）。
"""
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from app.tools.base import ToolPlugin, ToolRegistry

logger = logging.getLogger(__name__)


class FinishFrame(ToolPlugin):
    name = "finish_frame"
    description = (
        "状态帧的后事交接完毕，销掉这帧（**帧唯一的删除点**）。\n"
        "什么时候用：摘要里提示「⏳ 待交接 N 帧」、你用 list_states 看过、"
        "并且已经把该帧遗留的事处理完（记忆的作用域/锚点、要做的事、要记的东西）。\n"
        "处理后事一般要做的事：这段状态里的记忆该改锚点的改锚点（switch_focus / merge_focus）、"
        "该重新记的用 store_memory 记一条指对作用域的、不要的交给 manage_records 处置。\n"
        "边界：还在运行的状态帧销不掉——那个先用 close_state 关掉。"
    )
    segment = "self_management"
    parameters = {
        "frame_id": {"type": "string", "description": "要销掉的状态帧 id（list_states 里能看到）"},
    }
    required = ["frame_id"]
    states = ["active", "dnd", "inactive"]
    admin_description = "AI 声明某状态帧的后事已交接完毕，平台删除该帧记录"
    trigger_condition = "AI 处理完一个已结束状态留下的后事时"

    async def execute(self, db: AsyncSession, agent_id: int, group_id: int | None,
                      arguments: dict, context: dict) -> dict:
        from app.services.agent import focus_service
        from app.services.agent.state_stack_service import finish_frame
        from app.services.history.context_sync import append_events
        from app.utils.pure.history import make_entry

        frame_id = str(arguments.get("frame_id") or "").strip()
        if not frame_id:
            return {"error": True, "message": "要给它 frame_id（list_states 里能看到）"}

        ok, message = await finish_frame(db, agent_id, frame_id)
        if not ok:
            return {"error": True, "message": message}

        # 「已处置」落历史（进前缀，只写一次，之后每轮命中缓存）；解锁时随一次性条目离场
        ref = focus_service.current_ref(group_id, context or {})
        if ref:
            await append_events(db, agent_id, ref, [make_entry(
                "notice", f"（状态帧 {frame_id} 的后事已交接完毕，记录已销。）",
                flags={"drop_on_unlock": True},
            )])
        return {"success": True, "message": message}


ToolRegistry.register(FinishFrame)
