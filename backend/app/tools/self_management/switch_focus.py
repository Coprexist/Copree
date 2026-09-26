"""
switch_focus 工具 — 焦段的选、建、改名与归入
"""
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from app.tools.base import ToolPlugin, ToolRegistry
from app.services.agent import focus_service
from app.services.agent.state_stack_service import (
    get_active_semantic_focus, set_active_semantic_focus,
)
from app.utils.pure import focus as pure

logger = logging.getLogger(__name__)


class SwitchFocus(ToolPlugin):
    name = "switch_focus"
    description = (
        "管理你的焦段——记忆的适用范围。共有两条轴：\n"
        "- 会话焦段：一组同类对话（如「学生群」「所有私信」）。挂上去之后，"
        "写给这条轴的记忆在这些会话里都能被想起；不挂就只算当前这一个会话。\n"
        "- 语义焦段：一段话题（如「化学教学」「代码实现」）。它跨会话共享——"
        "在私信里讲过的化学，回到群里聊同一个话题也带得出来。\n"
        "用法（action）：\n"
        "- select：切换当前会话的语义焦段（focus_id）\n"
        "- create：新建焦段（name + axis，axis 默认 semantic）\n"
        "- rename：给焦段改名（focus_id + new_name）\n"
        "- join：把当前会话归入某个会话焦段（focus_id）——这是显式动作，"
        "「这是个私信」不等于「它属于所有私信焦段」，归不归由你判断\n"
        "- leave：把当前会话移出某个会话焦段（focus_id）\n"
        "焦段不宜多、不宜相似：相近的请用 merge_focus 合并，再改名。"
    )
    segment = "self_management"
    parameters = {
        "action": {"type": "string", "enum": ["select", "create", "rename", "join", "leave"],
                   "description": "要做的动作"},
        "focus_id": {"type": "string", "nullable": True,
                     "description": "焦段 id（select/rename/join/leave 用，list_focus 里能看到）"},
        "name": {"type": "string", "nullable": True, "description": "新建焦段的名字（create 用）"},
        "new_name": {"type": "string", "nullable": True, "description": "新名字（rename 用）"},
        "axis": {"type": "string", "enum": ["session", "semantic"], "nullable": True,
                 "description": "建在哪条轴上（create 用，默认 semantic）"},
    }
    required = ["action"]
    states = ["active", "dnd", "inactive"]
    admin_description = "焦段的选、建、改名与归入（记忆的适用范围）"
    trigger_condition = "AI 需要切换话题焦点、或把一个会话归入某类对话时"

    async def execute(self, db: AsyncSession, agent_id: int, group_id: int | None,
                      arguments: dict, context: dict) -> dict:
        action = arguments["action"]
        focus_id = (arguments.get("focus_id") or "").strip()
        ref = focus_service.current_ref(group_id, context)

        if action == "create":
            foci, _, msg = await focus_service.create(
                db, agent_id, arguments.get("name") or "", arguments.get("axis") or pure.SEMANTIC)
        elif action == "rename":
            foci, _, msg = await focus_service.rename(
                db, agent_id, focus_id, arguments.get("new_name") or "")
        elif action == "join":
            foci, _, msg = await focus_service.join_session(db, agent_id, focus_id, ref)
        elif action == "leave":
            foci, _, msg = await focus_service.leave_session(db, agent_id, focus_id, ref)
        elif action == "select":
            foci = await focus_service.load(db, agent_id)
            focus = pure.find(foci, focus_id)
            if focus is None or focus.get("axis") != pure.SEMANTIC:
                return {"error": True, "message": f"没找到语义焦段 {focus_id}"}
            await set_active_semantic_focus(db, agent_id, focus_id)
            msg = f"当前语义焦段已切到「{focus['name']}」"
        else:
            return {"error": True, "message": f"未知动作 {action}"}

        await db.commit()
        current = await get_active_semantic_focus(db, agent_id)
        return {"success": True, "message": msg,
                "focuses": pure.brief(foci, ref, current)}


ToolRegistry.register(SwitchFocus)
