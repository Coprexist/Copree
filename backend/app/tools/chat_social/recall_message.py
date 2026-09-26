"""
recall_message 工具 — AI 撤回自己在群里刚发出的消息

为什么要给 AI 这个工具：它说错话时只能"再解释一遍"，而群里那条错话还挂着、别的 AI
也照着它办事。有了撤回，站内那条变成「撤回了一条消息」，看过它的 AI 会收到作废通知。
"""
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.tools.base import ToolPlugin, ToolRegistry

logger = logging.getLogger(__name__)


class RecallMessage(ToolPlugin):
    name = "recall_message"
    description = (
        "撤回你在群里刚发出的某条消息（2 分钟内，只能撤自己的）。"
        "说错话、发错内容、把不该说的说出去了就用它：群里那条会变成「你撤回了一条消息」，"
        "已经看过它的其他 AI 会收到一条「这条已作废」的通知。超过 2 分钟撤不回来；"
        "如果那条同时发到了外部聊天软件（QQ），那边可能因为超时/权限撤不掉，结果里会写清楚。"
    )
    segment = "chat_social"
    parameters = {
        "group_id": {"type": "integer", "description": "目标群聊 ID"},
        "message_id": {"type": "integer", "description": "要撤回的消息 ID（必须是你自己发的）"},
    }
    required = ["group_id", "message_id"]
    states = ["active"]
    admin_description = "撤回 AI 自己刚发的群消息（2 分钟内）。发现说错了时自动调用。"
    trigger_condition = "AI 发现自己刚发的内容有错、需要撤回时"

    async def execute(self, db: AsyncSession, agent_id: int, group_id: int | None,
                      arguments: dict, context: dict) -> dict:
        from app.chat.revoke import RevokeDenied, RevokeExpired, revoke_group_message
        from app.models.agent import Agent as AgentModel
        from app.models.message import Message

        target_group = int(arguments.get("group_id") or group_id or 0)
        message_id = arguments["message_id"]

        agent = (await db.execute(select(AgentModel).where(AgentModel.id == agent_id))).scalar_one_or_none()
        if agent is None or agent.user_id is None:
            return {"error": True, "message": "AI 尚未初始化统一 ID，请稍后再试"}

        message = await db.get(Message, message_id)
        if message is None or int(message.group_id) != target_group:
            return {"error": True, "message": "这个群里没有这条消息"}

        try:
            # is_admin 恒为 False：AI 只能撤自己发的——哪怕它是群主 AI，也不替人做主
            result = await revoke_group_message(db, message, actor_id=int(agent.user_id), is_admin=False)
        except RevokeDenied as e:
            return {"error": True, "message": str(e)}
        except RevokeExpired as e:
            return {"error": True, "message": str(e)}
        await db.commit()

        manager = context.get("manager")
        if manager:
            try:
                await manager.broadcast_to_group(target_group, {
                    "type": "message_revoked",
                    "conversation_type": "group",
                    "data": {"id": message_id, "group_id": target_group},
                })
            except Exception as e:
                logger.warning(f"撤回广播失败（非致命）: {e}")

        # 通道侧结果照实说：站内撤了，QQ 那侧可能因为超时/权限撤不掉
        failed = [c for c in (result.get("channel") or []) if not c.get("ok")]
        return {
            "success": True,
            "message_id": message_id,
            "channel": result.get("channel") or [],
            "message": "已撤回" + (f"；但通道侧没撤掉：{failed}" if failed else ""),
        }


ToolRegistry.register(RecallMessage)
