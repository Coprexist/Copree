"""
read_conversation 工具 — 读**别的会话**（群/私信）最近的原文

为什么需要它：send_gm / send_dm 早就能发到任意目标，但 AI 不知道那边发生了什么，只能盲发。
账本按会话分卷、只追加，读尾部就是"那边最近 N 条原文"——写与读合起来才叫跨对话回复。
"""
import logging

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.tools.base import ToolPlugin, ToolRegistry

logger = logging.getLogger(__name__)

DEFAULT_LIMIT = 10
MAX_LIMIT = 30


class ReadConversation(ToolPlugin):
    name = "read_conversation"
    description = (
        "读**别的会话**（群聊或私信）最近几条原文，用来跨对话回复前先看清那边发生了什么。\n"
        "只读、不切换状态：看群传 group_id，看私信传 target_user_id（对方的 users.id）。\n"
        "要说话仍用 send_gm / send_dm（本工具不会发消息）。"
    )
    segment = "chat_social"
    parameters = {
        "group_id": {"type": "integer", "nullable": True, "description": "要看的群聊 ID"},
        "target_user_id": {"type": "integer", "nullable": True, "description": "要看的私信对象 users.id"},
        "limit": {"type": "integer", "nullable": True, "description": f"看最近几条（默认 {DEFAULT_LIMIT}，最多 {MAX_LIMIT}）"},
    }
    required = []
    states = ["active"]
    admin_description = "读取其它会话最近的消息原文（只读，用于跨会话回复前了解上下文）。"
    trigger_condition = "AI 要回复别的会话、但不知道那边说了什么时"

    async def execute(self, db: AsyncSession, agent_id: int, group_id: int | None,
                      arguments: dict, context: dict) -> dict:
        from app.chat.dm import DMSession
        from app.models.agent import Agent as AgentModel
        from app.services.history import history_service as hs
        from app.services.history.context_sync import context_ref

        agent = (await db.execute(
            select(AgentModel).where(AgentModel.id == agent_id))).scalar_one_or_none()
        agent_user_id = getattr(agent, "user_id", None)

        want_group = arguments.get("group_id")
        want_user = arguments.get("target_user_id")
        limit = min(MAX_LIMIT, max(1, int(arguments.get("limit") or DEFAULT_LIMIT)))

        if want_group:
            ref, label = context_ref(group_id=int(want_group)), f"群聊#{want_group}"
        elif want_user and agent_user_id:
            # 会话 id 用库里存的真相，不在这里重算规则（重算就是抄第二份）
            target = int(want_user)
            ref = (await db.execute(select(DMSession.session_id).where(or_(
                and_(DMSession.user1_id == agent_user_id, DMSession.user2_id == target),
                and_(DMSession.user1_id == target, DMSession.user2_id == agent_user_id),
            )).limit(1))).scalar_one_or_none()
            if not ref:
                return {"success": False, "message": f"你和用户 {target} 没有私信会话"}
            label = f"与用户 {target} 的私信"
        else:
            return {"success": False,
                    "message": "要指定看哪个会话：群传 group_id，私信传 target_user_id"}

        entries = await hs.tail(db, agent_id, ref, limit)
        if not entries:
            return {"success": False, "message": f"{label} 还没有可读的历史（你还没在那个会话里说过话）"}
        total = await hs.count(db, agent_id, ref)

        lines = [
            f"## {label} 最近的 {len(entries)} 条（该会话账本共 {total} 条）",
            "（这是**别的会话**里的历史，不是当前这里发生的事；要在那边说话用 send_gm / send_dm。）",
        ]
        lines += [f"- {e['content']}" for e in entries]
        return {"success": True, "content": "\n".join(lines), "context_ref": ref, "count": len(entries)}


ToolRegistry.register(ReadConversation)