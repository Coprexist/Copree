"""note_experience 工具 — 让 AI 自己记一笔「我在哪儿干了什么」

和 end_turn 的 key_note 是同一张表、同一套投递，只是它是显式的：key_note 跟着每轮收尾走，
这里用来补那种「这轮没什么工具动作，但这件事以后还会问到」的场合。

为什么不并进 store_memory / manage_records：
- 向量记忆是「问起才想起」，会被衰减与相关性限制；经历行是「去过哪」的指针，进过某个
  上下文就不再投，且不参与衰减；
- 结构记忆是 AI 自己的键值树，平台不保证它写过，也不负责跨场景补进来。
"""
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.tools.base import ToolPlugin, ToolRegistry

logger = logging.getLogger(__name__)


class NoteExperience(ToolPlugin):
    name = "note_experience"
    persistent_effect = True   # 写进持久状态：结果要进账本，否则下一个自己不知道写了什么
    description = (
        "记一笔「我在这个会话里干了什么」——以后换到别的群/私信，那边的你会想起这件事。\n"
        "和 end_turn 的 key_note 同一张表：那一条跟着每轮收尾走，这个用来补「这轮没干什么"
        "但值得记」的场合；重复写同一件事会各留一行，所以先 list 看一眼再写。\n"
        "写什么：场景 + 做了什么 + 结论/下一步，一两句（例：把 world_chat_service 的阈值"
        "从 300 调到 500，因为下午的测试里 300 会误伤正常的追问）。\n"
        "不要写：别人的姓名与隐私、金额、群里的私事、只在当前群成立的暗号与约定——"
        "这行会被别的会话里的你看到。\n"
        "长期事实（人设、关系、偏好）仍写 store_memory / update_self_config，别塞这里。\n"
        "用法：add 记一笔 / list 看最近记过什么。"
    )
    segment = "self_management"
    parameters = {
        "action": {"type": "string", "enum": ["add", "list"],
                   "description": "add=记一笔, list=看最近记过什么"},
        "what": {"type": "string", "nullable": True,
                 "description": "这一笔干了什么（add 时必填，一两句，含结论或下一步）"},
    }
    required = ["action"]
    states = ["active", "dnd", "inactive"]
    admin_description = "自己记一笔经历：写进跨情景经历表，别的会话里的自己能被想起（进过就不再投）。"
    trigger_condition = "AI 认为这件事以后在别的会话里还会用到时"

    async def execute(self, db: AsyncSession, agent_id: int, group_id: int | None,
                      arguments: dict, context: dict) -> dict:
        from app.models.agent import Agent
        from app.services.experience import experience_service
        from app.services.history.context_unlock import context_ref_for

        action = arguments["action"]
        try:
            from app.models.agent_experience import OWNER_AI

            agent = await db.get(Agent, agent_id)
            if agent is None:
                return {"error": True, "message": f"AI {agent_id} 不存在"}
            conv_ref = context_ref_for(
                context.get("conversation_type") or "",
                group_id=group_id, session_id=context.get("session_id"),
            )
            # 场景取当前帧：插件帧里记的事落在插件帧上，不落回它所在的群
            from app.services.agent.state_stack_service import current_scene

            scene_info = await current_scene(db, agent_id)
            scene = scene_info.get("context_ref") or conv_ref

            if action == "add":
                summary = (arguments.get("what") or "").strip()
                if not summary:
                    return {"error": True, "message": "what 不能为空：写清这一笔干了什么"}
                row = await experience_service.record(
                    db, owner_type=OWNER_AI, owner_id=agent_id, context_ref=scene,
                    group_id=group_id, me_user_id=agent.user_id or 0, summary=summary,
                    source=experience_service.SOURCE_AI,
                    frame_type=scene_info.get("type") or "",
                    label=scene_info.get("label") or "",
                )
                if row is None:
                    return {"error": True, "message": "没写成（摘要为空或落库失败）"}
                return {"success": True, "id": row["id"],
                        "message": "记下了；换到别的会话时，那边的你能想起这一笔"}

            if action == "list":
                return {"success": True,
                        "recent": await experience_service.recent(
                            db, OWNER_AI, agent_id, 10, exclude_scene=scene)}

            return {"error": True, "message": f"未知操作 {action}"}
        except Exception as e:
            logger.error(f"note_experience 失败 (agent={agent_id}): {e}", exc_info=True)
            return {"error": True, "message": f"记经历失败: {e}"}


ToolRegistry.register(NoteExperience)
