"""expand_context 工具 — 展开被上下文压缩折叠掉的那一段原文。

为什么需要它：压缩只留一句「中间 N 条已折叠」。压缩之后 AI 可能记得
「前面有个结论」，却拿不到原话——而平台一直在提示它调一个**并不存在的工具**
（2026-11-25 排查确认：全仓只有那句文案，没有实现）。要么给工具，要么别指路。

边界：这是「按需回看」，不是「补回上下文」。展开的内容随本次工具结果进入上下文，
下轮压缩时该折的照样折——否则等于把压缩白做一遍。
"""
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.tools.base import ToolPlugin, ToolRegistry

logger = logging.getLogger(__name__)

# 展开一次最多带回多少字：一个折叠段可能有几万字，全塞回去 token 立刻爆
MAX_CHARS = 20_000
DEFAULT_LIMIT = 40
MAX_LIMIT = 120


def _render_message(msg: dict) -> str:
    """一条消息折成一行文本（工具调用保留名字与参数摘要，参数过长截断）。"""
    role = msg.get("role") or "?"
    content = str(msg.get("content") or "").strip()
    parts = [f"[{role}] {content}" if content else f"[{role}]"]
    for tc in msg.get("tool_calls") or []:
        fn = (tc or {}).get("function") or {}
        name = fn.get("name") or "?"
        args = str(fn.get("arguments") or "")
        if len(args) > 300:
            args = args[:300] + "…"
        parts.append(f"  → 调用 {name}({args})")
    return "\n".join(parts)


class ExpandContext(ToolPlugin):
    name = "expand_context"
    description = (
        "展开被上下文压缩折叠掉的那一段原文。\n"
        "压缩时会给一个段号（形如 #a1b2c3d4）——传它就能看到折叠前的对话。\n"
        "只用于「我得确认前面到底原话说了什么」，不是补回上下文：看完该压缩的还会压缩。"
    )
    segment = "memory"
    parameters = {
        "segment_id": {
            "type": "string",
            "description": "压缩提示里给的段号（不带 # 也可以）",
        },
        "limit": {
            "type": "integer",
            "nullable": True,
            "description": f"最多带回几条消息（默认 {DEFAULT_LIMIT}，最多 {MAX_LIMIT}）",
        },
    }
    required = ["segment_id"]
    states = ["active"]
    admin_description = "展开被上下文压缩折叠的原文（按段号回看，只读）。"
    trigger_condition = "压缩提示说有 N 条被折叠、而 AI 需要确认前面原话时"

    async def execute(self, db: AsyncSession, agent_id: int, group_id: int | None,
                      arguments: dict, context: dict) -> dict:
        from app.services.history import context_segment_service as seg_svc

        raw = str(arguments.get("segment_id") or "").strip().lstrip("#")
        if not raw:
            return {"error": True, "message": "需要 segment_id（压缩提示里那个段号）"}

        segment = await seg_svc.load(db, agent_id=agent_id, segment_id=raw)
        if segment is None:
            return {
                "error": True,
                "message": (
                    f"段 {raw} 不存在或已过期（折叠原文保留 7 天）。"
                    "如果只是想看某段对话说了什么，用 read_conversation 读最近的原文。"
                ),
            }

        limit = min(MAX_LIMIT, max(1, int(arguments.get("limit") or DEFAULT_LIMIT)))
        messages = segment["messages"][-limit:]
        lines: list[str] = []
        used = 0
        truncated = False
        for msg in messages:
            line = _render_message(msg)
            if used + len(line) > MAX_CHARS:
                truncated = True
                break
            lines.append(line)
            used += len(line)

        head = (
            f"段 {segment['id']}：折叠于 {segment['created_at'][:19]}，"
            f"共 {segment['message_count']} 条（约 {segment['token_count']} tokens）。"
            f"下面是其中最后 {len(lines)} 条原文。"
        )
        tail = ""
        if truncated:
            tail = (
                f"\n\n（后面还有内容，已按 {MAX_CHARS} 字截断——"
                "需要更早的具体某条，用 limit 指定条数，或 read_conversation 读会话原文。）"
            )
        logger.info(
            "AI(%s) 展开压缩段 %s：%s/%s 条",
            agent_id, segment["id"], len(lines), segment["message_count"],
        )
        return {
            "success": True,
            "segment_id": segment["id"],
            "returned": len(lines),
            "total": segment["message_count"],
            "messages": lines,
            "content": head + "\n\n" + "\n\n".join(lines) + tail,
        }


ToolRegistry.register(ExpandContext)
