"""
read_manual 工具 — AI 查看平台文档

读的是 docs 下那几份给人和 AI 共用的说明：默认用户手册（功能与消息格式），
也可以读《创建 AI 参考》（人设怎么写）。检索只有一份实现，在 services/docs/library。
"""
import logging

from app.tools.base import ToolPlugin, ToolRegistry

logger = logging.getLogger(__name__)


class ReadManual(ToolPlugin):
    name = "read_manual"
    description = (
        "查看平台文档，获取准确说法而不是凭记忆答。"
        "doc=manual（默认）是《用户手册》：平台功能、消息格式（彩色文字、Markdown）、文件转发、闹钟、"
        "记忆、联邦等；doc=ai-guide 是《创建 AI 参考》：AI 人设怎么写（四层结构、长度、去 AI 感、模板、自检）。"
        "可传关键词（如「彩色文字」「闹钟」「人设」），不传则返回目录概览。"
        "用户问平台怎么用、或你要写/改 AI 的性格描述时，先查再答。"
    )
    segment = "chat_social"
    parameters = {
        "doc": {
            "type": "string",
            "enum": ["manual", "ai-guide"],
            "description": "哪份文档：manual=用户手册（默认），ai-guide=创建 AI 参考",
        },
        "keyword": {
            "type": "string",
            "description": "搜索关键词（可选）。如「彩色文字」「文件转发」「闹钟」「人设」。不传则返回文档目录。",
        },
    }
    required = []
    states = ["active", "dnd"]
    admin_description = "AI 查看平台文档（用户手册 / 创建 AI 参考），支持关键词搜索。"

    async def execute(self, db, agent_id, group_id, arguments, context):
        from app.services.docs import library

        return library.read(
            (arguments.get("doc") or "manual").strip(),
            (arguments.get("keyword") or "").strip(),
        )


ToolRegistry.register(ReadManual)
