"""
forget_memory 工具 — AI 删掉一条自己存的向量记忆

为什么单独一个工具而不是塞进 store_memory：删是不可逆的，跟"写/改"分开，
工具闸与审计（谁在什么时候删了哪条）才看得见；`reason` 落日志，给他日后一个交代。
"""
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from app.tools.base import ToolPlugin, ToolRegistry

logger = logging.getLogger(__name__)


class ForgetMemory(ToolPlugin):
    name = "forget_memory"
    description = (
        "删掉一条你自己存的向量记忆：记错了、两条重复留了旧的、或者这件事已经过去了不必再提。\n"
        "分辨一下再动手：\n"
        "· 只是**记岔了**（锚点、权值、标题、正文要改）——用 store_memory 带 memory_id 改，"
        "换来的还是同一条记忆；\n"
        "· 真的**不要了**才用这个——删了就没了，下次想不起来就是真的想不起来。\n"
        "id 从 recall_memory 的返回里拿；拿不准就先 recall 看清标题与内容再删。"
    )
    segment = "memory"
    parameters = {
        "memory_id": {"type": "integer", "description": "要删的那条记忆 id（recall_memory 的返回里有）"},
        "reason": {
            "type": "string", "nullable": True,
            "description": "为什么删（写进日志；日后你自己回想「这条怎么没了」时有据可查）",
        },
    }
    required = ["memory_id"]
    states = ["active"]
    admin_description = "删除一条自己存的向量记忆（不可逆）。整理记忆用：重复的、记错的、过期不要的。"
    trigger_condition = "AI 发现某条记忆已无保留价值或记错了，且改不如删时"

    async def execute(self, db: AsyncSession, agent_id: int, group_id: int | None,
                      arguments: dict, context: dict) -> dict:
        from app.services.memory import memory_service

        memory_id = int(arguments["memory_id"])
        out = await memory_service.forget_memory(db, agent_id, memory_id)
        if not out.get("ok"):
            return {"error": True, "message": out.get("message", "没删成")}
        logger.info(f"AI({agent_id}) 删除记忆 {memory_id}「{out['title']}」："
                    f"{str(arguments.get('reason') or '未说明')[:120]}")
        from app.services.memory.memory_service import note_memory_changed
        await note_memory_changed(agent_id, group_id)
        return {"success": True, "id": out["id"], "title": out["title"],
                "message": f"已删掉「{out['title']}」"}


ToolRegistry.register(ForgetMemory)
