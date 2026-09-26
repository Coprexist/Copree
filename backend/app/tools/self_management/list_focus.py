"""
list_focus 工具 — 查看焦段清单
"""
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from app.tools.base import ToolPlugin, ToolRegistry
from app.services.agent import focus_service
from app.services.agent.state_stack_service import get_active_semantic_focus
from app.utils.pure import focus as pure

logger = logging.getLogger(__name__)


class ListFocus(ToolPlugin):
    name = "list_focus"
    description = (
        "查看你的焦段清单：每条的 id / 名字 / 在哪条轴 / 挂了多少个会话 / 是不是当前命中。\n"
        "焦段不宜多、不宜相似——重复或相近的用 merge_focus 合并（合并前会先把两边的记忆"
        "列给你看），名字不合适就用 switch_focus 的 rename 改。"
    )
    segment = "self_management"
    parameters = {}
    states = ["active", "dnd", "inactive"]
    admin_description = "查看焦段清单（记忆的适用范围）"
    trigger_condition = "AI 想确认自己有哪些焦段、或准备合并 / 改名时"

    async def execute(self, db: AsyncSession, agent_id: int, group_id: int | None,
                      arguments: dict, context: dict) -> dict:
        foci = await focus_service.load(db, agent_id)
        current = await get_active_semantic_focus(db, agent_id)
        ref = focus_service.current_ref(group_id, context)
        return {
            "success": True,
            "focuses": pure.brief(foci, ref, current),
            "hint": "没挂会话焦段的会话只算当前状态；要让记忆跨会话可用，先把它归入某个会话焦段。",
        }


ToolRegistry.register(ListFocus)
