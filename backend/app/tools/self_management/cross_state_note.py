"""cross_state_note 工具 — 跨状态便签（临时、有时效的跨会话留言）

为什么单独一个工具、而不并进 manage_workspace：
TODO/PLAN/JOURNAL 是「状态内」的（跟着一段对话、一件事走），拿它们跨会话会污染
工作区语义。真正需要跨状态传递的只是临时且有时效的一句提醒，所以单开一张便签。
"""
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from app.tools.base import ToolPlugin, ToolRegistry

logger = logging.getLogger(__name__)


class CrossStateNote(ToolPlugin):
    name = "cross_state_note"
    description = (
        "跨状态便签：留给「别的会话/别的状态」的一句临时提醒。\n"
        "只写**临时、有时效**的事（例：私信里和人约好暗号，等会儿群里有人问就照答）。\n"
        "什么时候用我：用户说「记一下」「等会儿到群里/别的对话要…」这类**临时约定**时。\n"
        "换会话时，落在有效期内的便签会**自动进别的会话的上下文**；而 manage_records 只把"
        "路径注入上下文、值要再 get 一次——临时约定写那儿会丢（2026-09-25 实测丢过暗号）。\n"
        "规则：\n"
        "- 写下后 40 次 API 调用内有效，超过自动作废；\n"
        "- 只有**别的**状态/会话会拿到，本会话自己写自己看不到；\n"
        "- 有效期内投进哪个会话，就归那段对话的上下文管了（不重复花 token）："
        "过期只是不再投给新会话，已经拿到的那份不动；删掉记录则会在已投递的会话里发一条"
        "「便签撤下通知」——只发一次，原来的行不改、以通知为准；要让它整个离开那段对话只能等 compact/清空；\n"
        "- 长期要记住的事（人设、关系、偏好、长期目标）不要写这里，"
        "请用 update_self_config 更新你自己的提示词。\n"
        "用法：add 留一句 / list 看现有（含剩余条数）/ update 改 / remove 删 / clear 清空。"
    )
    segment = "self_management"
    parameters = {
        "action": {"type": "string", "enum": ["list", "add", "update", "remove", "clear"],
                   "description": "list=看现有便签, add=留一句, update=改内容, remove=删一条, clear=全清"},
        "text": {"type": "string", "nullable": True, "description": "便签内容（add/update 时必填，一句话）"},
        "kind": {"type": "string", "enum": ["todo", "plan"], "nullable": True,
                 "description": "便签类型：todo=待办提醒（默认）, plan=短程打算"},
        "note_id": {"type": "string", "nullable": True, "description": "便签 id（update/remove 时必填，list 里能看到）"},
    }
    required = ["action"]
    states = ["active", "dnd", "inactive"]
    admin_description = "跨状态便签：临时、有时效的跨会话留言（40 次 API 调用内可投递；投进某会话后固化在它的上下文里，直到那段对话 compact）。"
    trigger_condition = "AI 需要把一句临时提醒带到别的会话时"

    async def execute(self, db: AsyncSession, agent_id: int, group_id: int | None,
                      arguments: dict, context: dict) -> dict:
        from app.services.agent import cross_state_note_service as svc

        action = arguments["action"]
        try:
            if action == "list":
                return {"success": True, "notes": await svc.list_notes(db, agent_id)}

            if action == "add":
                _, msg = await svc.add_note(
                    db, agent_id, arguments.get("text", ""), arguments.get("kind") or "todo")
                await db.commit()
                return {"success": True, "message": msg,
                        "notes": await svc.list_notes(db, agent_id)}

            if action == "update":
                notes, msg = await svc.update_note(
                    db, agent_id, (arguments.get("note_id") or "").strip(),
                    arguments.get("text") or "", arguments.get("kind") or "")
                await db.commit()
                return {"success": True, "message": msg}

            if action == "remove":
                notes, msg = await svc.remove_note(db, agent_id, (arguments.get("note_id") or "").strip())
                await db.commit()
                return {"success": True, "message": msg}

            if action == "clear":
                await svc.clear_notes(db, agent_id)
                await db.commit()
                return {"success": True, "message": "便签已清空"}

            return {"error": True, "message": f"未知操作 {action}"}
        except Exception as e:
            logger.error(f"cross_state_note 失败 (agent={agent_id}): {e}", exc_info=True)
            return {"error": True, "message": f"便签操作失败: {e}"}


ToolRegistry.register(CrossStateNote)
