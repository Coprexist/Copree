"""
merge_focus 工具 — 合并两个焦段（执行前先列清两边的记忆）
"""
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from app.tools.base import ToolPlugin, ToolRegistry
from app.services.agent import focus_service

logger = logging.getLogger(__name__)


class MergeFocus(ToolPlugin):
    name = "merge_focus"
    description = (
        "合并两个焦段：把 drop_id 的名字与元素并进 keep_id，随后删掉 drop_id；"
        "已经锚在被并入那一侧的**记忆锚点会一起改指过来**，不会留下指向空焦段的记忆。\n"
        "**第一次调用不要带 confirm**：它会先把两边挂着的记忆列给你看（这是硬要求——"
        "不看清楚就合并，等于把两段经历揉成一团）。看完确认要合，再带 confirm=true 调第二次。\n"
        "合完的名字不合适，再用 switch_focus 的 rename 改。"
    )
    segment = "self_management"
    parameters = {
        "keep_id": {"type": "string", "description": "保留的焦段 id"},
        "drop_id": {"type": "string", "description": "被并入并删掉的焦段 id"},
        "confirm": {"type": "boolean", "nullable": True,
                    "description": "看过两边的记忆、确认执行时才填 true"},
    }
    required = ["keep_id", "drop_id"]
    states = ["active", "dnd", "inactive"]
    admin_description = "合并焦段（执行前强制列出两侧锚定的记忆）"
    trigger_condition = "AI 发现两个焦段重复或相近时"

    async def execute(self, db: AsyncSession, agent_id: int, group_id: int | None,
                      arguments: dict, context: dict) -> dict:
        keep_id = (arguments.get("keep_id") or "").strip()
        drop_id = (arguments.get("drop_id") or "").strip()

        if not arguments.get("confirm"):
            keep = await focus_service.anchored_memories(db, agent_id, keep_id)
            drop = await focus_service.anchored_memories(db, agent_id, drop_id)
            return {
                "success": True,
                "need_confirm": True,
                "keep": keep,
                "drop": drop,
                "message": (
                    f"合并前先看清两边：保留的一侧有 {len(keep)} 条记忆，"
                    f"将被并入的一侧有 {len(drop)} 条。确认要合，把 confirm 设为 true 再调一次——"
                    "合完这些记忆的锚点会一起改到保留的焦段上。"
                ),
            }

        foci, msg = await focus_service.merge(db, agent_id, keep_id, drop_id)
        await db.commit()
        return {"success": True, "message": msg}


ToolRegistry.register(MergeFocus)
