"""
end_turn 工具 — 结束本轮（轮末结算）：本轮到此为止，并交代思考留不留。

设计见 docs/dev/conversation_history.md §5：
- 语义是通用的"结束本轮 + 结算"，"把发言权交还给对方"只是群聊/私信场景下的效果；
- `keep_thinking`：本轮思考原文要不要留给后面的自己（默认 false = 不保留，省 token）；
- `key_note`：要留给后面自己的关键信息（为什么这么定、结论、下一步）；
- 两个参数都省着走默认，兜底逻辑在平台侧（永不空：正文本身就是"干了什么"）。
"""
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from app.tools.base import ToolPlugin, ToolRegistry

logger = logging.getLogger(__name__)


class EndTurn(ToolPlugin):
    name = "end_turn"
    description = (
        "结束本轮（轮末结算）：本轮到此为止，不会再触发后续 API。"
        "同时交代本轮的思考留不留、以及要留给后面自己的关键信息。\n"
        "在群聊/私信里，它的效果是把发言权交还给对方；在任务型对话里，就是本轮收尾。\n"
        "参数都可省，省了按「不保留」处理：\n"
        "- keep_thinking：本轮的思考要不要留给后面的自己（true=留，跨轮还能看到；false/省略=思考与工具过程都不留）；\n"
        "- key_note：不保留时补一句关键信息（为什么这么定、结论、下次接着干什么）。\n"
        "不保留时，只有你发的正文、key_note、以及未完成的事会留下——平台会给后面的自己一条"
        "简要说明（本轮调用过什么、结果如何），所以细节要写进 key_note，否则再遇到"
        "「为什么这么改 / 改到哪了」就得重新翻一遍文件。\n"
        "什么时候选 true：你还有想法或事情没表达完、但又必须暂时收尾（在等对方回话、在等工具结果、被打断）。\n"
        "边界：思考只在当前这段上下文里有效，不会传给别的状态——别的群、别的私信、别的世界会话都看不到它。"
    )
    segment = "self_management"
    parameters = {
        "keep_thinking": {"type": "boolean", "nullable": True,
                          "description": "本轮的思考要不要留在当前上下文里（默认 false：省 token）"},
        "key_note": {"type": "string", "nullable": True,
                     "description": "留给后面自己的关键信息（不保留思考时尤其该填：为什么这么定 / 结论 / 下一步）"},
    }
    required = []
    states = ["active", "dnd", "inactive"]
    admin_description = "结束本轮（轮末结算）：终止后续 API 触发，并记录本轮的思考保留决定与关键信息。"
    trigger_condition = "AI 认为本轮已可收尾时"

    async def execute(self, db: AsyncSession, agent_id: int, group_id: int | None,
                      arguments: dict, context: dict) -> dict:
        keep = bool(arguments.get("keep_thinking"))
        key_note = (arguments.get("key_note") or "").strip()
        return {
            "success": True,
            "end_turn": True,
            "keep_thinking": keep,
            "key_note": key_note,
            "message": "已结束本轮回复" + ("（思考保留）" if keep else ""),
        }


ToolRegistry.register(EndTurn)
