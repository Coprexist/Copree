"""search_messages 工具 — 按关键词搜 AI 能看到的聊天原文

为什么需要它：平台此前只有"按会话读最近 N 条"（read_conversation）和"按 id 展开"
（expand_message），没有关键词检索——用户说"你搜一下"时，AI 要么去搜网页（找不到站内的事），
要么凭上下文猜，最后演成"我搜了，没搜到"（2026-10-31 真机）。这个工具补的就是那一步。

可见范围只有一条判据：**AI 自己在的会话**——它已加入的群、它参与的私信。越界不给。
"""
import logging
from datetime import timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.tools.base import ToolPlugin, ToolRegistry

logger = logging.getLogger(__name__)

DEFAULT_LIMIT = 10
MAX_LIMIT = 30
DEFAULT_DAYS = 30
MAX_DAYS = 3650
SNIPPET_PAD = 24          # 命中词前后各留多少字
SNIPPET_MAX = 120


def _snippet(content: str, query: str) -> str:
    """命中词周围的一小段，别把整条长消息灌回上下文"""
    text = str(content or "").replace("\n", " ").strip()
    low, q = text.lower(), str(query or "").lower()
    pos = low.find(q)
    if pos < 0:
        return text[:SNIPPET_MAX]
    start = max(0, pos - SNIPPET_PAD)
    end = min(len(text), pos + len(q) + SNIPPET_PAD)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return (prefix + text[start:end] + suffix)[:SNIPPET_MAX]


class SearchMessages(ToolPlugin):
    name = "search_messages"
    description = (
        "按关键词搜**聊天原文**（你已经加入的群、你参与的私信）。"
        "群聊与私信一起搜，结果带会话、消息 id、时间与命中片段。"
        "可只搜一个群（group_id）、只搜某个人说的（sender_id）、只看最近几天（days）。"
        "用法提示：检索式取「最有辨识度的那个词」（专名、错别字少的名词），别把整句丢进去；"
        "换两三个说法各搜一次，比一次搜长句有效。要读上下文就用返回的 message_id 调 expand_message。"
        "注意：它搜不到网页上的东西——外部资料用 web_search；也不会替你总结，只给命中片段。"
    )
    segment = "chat_social"
    parameters = {
        "query": {"type": "string", "description": "关键词（一段短语也可以，按包含匹配）"},
        "group_id": {"type": "integer", "nullable": True,
                     "description": "只搜这个群聊（不传 = 你加入的所有群）"},
        "sender_id": {"type": "integer", "nullable": True,
                      "description": "只看这个人（或这个 AI）说的消息，传 users.id"},
        "days": {"type": "integer", "nullable": True,
                 "description": f"只看最近多少天（默认 {DEFAULT_DAYS}，最多 {MAX_DAYS}）"},
        "limit": {"type": "integer", "nullable": True,
                  "description": f"返回条数（1-{MAX_LIMIT}，默认 {DEFAULT_LIMIT}）"},
    }
    required: list = ["query"]
    states = ["active", "dnd"]
    admin_description = "按关键词搜 AI 可见的聊天原文（它加入的群与参与的私信），返回会话、消息 id、时间与命中片段。"
    trigger_condition = "需要回查聊天记录里某件事、某句话时"

    async def execute(self, db: AsyncSession, agent_id: int, group_id: int | None,
                      arguments: dict, context: dict) -> dict:
        query = str((arguments or {}).get("query") or "").strip()
        if not query:
            return {"success": False, "error": "query 必填：给一个关键词"}
        limit = max(1, min(int((arguments or {}).get("limit") or DEFAULT_LIMIT), MAX_LIMIT))
        days = max(1, min(int((arguments or {}).get("days") or DEFAULT_DAYS), MAX_DAYS))
        only_group = (arguments or {}).get("group_id")
        only_sender = (arguments or {}).get("sender_id")

        from app.models.agent import Agent as AgentModel
        from app.models.dm import DMMessage, DMSession
        from app.models.group import GroupMember
        from app.models.message import Message
        from app.utils.display_name import display_names
        from app.utils.pure.timeutil import utc_now

        agent = await db.get(AgentModel, agent_id)
        if agent is None:
            return {"success": False, "error": "找不到这个 AI 的记录"}
        me = int(agent.user_id or 0)
        since = utc_now() - timedelta(days=days)
        pattern = f"%{query}%"

        # 可见范围：自己在的群（member_type='ai' + 自己的 user_id）
        group_ids = [int(g) for g in (await db.execute(
            select(GroupMember.group_id).where(
                GroupMember.member_id == me, GroupMember.member_type == "ai")
        )).scalars().all()]
        if only_group:
            group_ids = [g for g in group_ids if g == int(only_group)]
        if group_id and group_id not in group_ids:
            pass          # 传了当前群也仍在可见范围内，不额外收窄

        results: list[dict] = []
        if group_ids:
            stmt = select(Message).where(
                Message.group_id.in_(group_ids),
                Message.content.ilike(pattern),
                Message.revoked_at.is_(None),
                Message.created_at >= since,
            )
            if only_sender:
                stmt = stmt.where(Message.sender_id == int(only_sender))
            rows = (await db.execute(
                stmt.order_by(Message.id.desc()).limit(limit)
            )).scalars().all()
            names = await display_names(db, {int(r.sender_id) for r in rows})
            for row in rows:
                results.append({
                    "conversation": f"group:{row.group_id}",
                    "message_id": int(row.id),
                    "sender_type": row.sender_type,
                    "sender_id": int(row.sender_id),
                    "sender_name": names.get(int(row.sender_id), ""),
                    "created_at": row.created_at.isoformat() if row.created_at else "",
                    "snippet": _snippet(row.content, query),
                })

        # 私信：只搜自己参与的会话
        if not only_group:
            session_ids = (await db.execute(
                select(DMSession.session_id).where(
                    or_(DMSession.user1_id == me, DMSession.user2_id == me))
            )).scalars().all()
            if session_ids:
                stmt = select(DMMessage).where(
                    DMMessage.session_id.in_(list(session_ids)),
                    DMMessage.content.ilike(pattern),
                    DMMessage.created_at >= since,
                )
                if only_sender:
                    stmt = stmt.where(DMMessage.sender_id == int(only_sender))
                rows = (await db.execute(
                    stmt.order_by(DMMessage.id.desc()).limit(limit)
                )).scalars().all()
                names = await display_names(db, {int(r.sender_id) for r in rows})
                for row in rows:
                    results.append({
                        "conversation": f"dm:{row.session_id}",
                        "message_id": int(row.id),
                        "sender_type": row.sender_type,
                        "sender_id": int(row.sender_id),
                        "sender_name": names.get(int(row.sender_id), ""),
                        "created_at": row.created_at.isoformat() if row.created_at else "",
                        "snippet": _snippet(row.content, query),
                    })

        results.sort(key=lambda item: item["created_at"], reverse=True)
        results = results[:limit]
        return {
            "success": True,
            "query": query,
            "count": len(results),
            "scope": {"days": days, "groups": len(group_ids), "group_id": only_group},
            "results": results,
            "note": "没有命中时换更短、更有辨识度的词再搜一次；外部资料请用 web_search。",
        }
