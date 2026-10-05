"""
compress_context 工具 — AI 主动压缩当前对话上下文

当 AI 感知到对话历史过长、信息密度降低时，可主动调用此工具
将中间消息压缩为摘要，释放上下文窗口空间。
"""
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from app.tools.base import ToolPlugin, ToolRegistry

logger = logging.getLogger(__name__)


class CompressContext(ToolPlugin):
    name = "compress_context"
    description = (
        "压缩当前对话上下文。当你觉得对话历史太长、信息过载，或之前的话题已经结束、"
        "不再需要完整细节时，调用此工具将中间消息压缩为摘要。"
        "压缩后：系统提示词和最近几条消息会保留，中间部分替换为简洁摘要。"
        "这能释放上下文窗口空间，让你可以继续处理新话题。"
    )
    segment = "self_management"
    parameters = {
        "keep_last_n": {
            "type": "integer",
            "description": "保留最近 N 条消息不压缩，默认 5。如果当前话题需要更多上下文，可以设大一些",
        },
        "force": {
            "type": "boolean",
            "description": "强制压缩（即使未达到自动压缩阈值）。如果觉得上下文太长但没到阈值，设为 true",
        },
    }
    required = []
    states = ["active", "dnd"]
    admin_description = "压缩当前对话上下文。AI 主动将旧消息总结为摘要以释放上下文窗口空间，保留系统提示词和最近消息。"
    trigger_condition = "上下文过长 / AI 主动清理对话历史时"

    async def execute(self, db: AsyncSession, agent_id: int, group_id: int | None,
                      arguments: dict, context: dict) -> dict:
        # 从 context 获取消息列表
        messages: list[dict] = context.get("_messages", [])
        if not messages:
            return {"error": True, "message": "无法获取当前对话上下文"}

        keep_last_n = arguments.get("keep_last_n", 5)
        force = arguments.get("force", False)

        # 检查是否需要压缩
        if not force:
            from app.services.memory.context_compression_service import should_compress, estimate_tokens
            if not should_compress(messages):
                return {
                    "compressed": False,
                    "reason": "当前上下文未达到压缩阈值，无需压缩。如确需压缩请设置 force=true",
                    "estimated_tokens": estimate_tokens(messages),
                }

        # 获取压缩配置
        api_key = context.get("api_key", "")
        api_base_url = context.get("api_base_url", "")
        model = context.get("_model", "deepseek-v4-pro")
        agent = context.get("_agent")
        user_id = str(agent.id) if agent else None

        if not api_key or not api_base_url:
            return {"error": True, "message": "缺少 API 配置，无法执行压缩"}

        # 执行压缩
        from app.services.memory.context_compression_service import compress_messages

        try:
            # 确保 keep_last_n 在合理范围
            keep_last_n = max(1, min(keep_last_n, 20))
            new_messages, stats = await compress_messages(
                messages=messages,
                api_base_url=api_base_url,
                api_key=api_key,
                model=model,
                keep_system=True,
                keep_last_n=keep_last_n,
                user_id=user_id,
            )

            if stats.get("compressed"):
                # 更新 context 中的消息引用（原地替换）
                messages.clear()
                messages.extend(new_messages)
                # 解锁点：只换内存里的数组等于没压——下一轮 build_messages 又从账本把原文端回来。
                # 线上实测（2026-10-04 群 69）：AI 自己 compact 完当轮 13 条，下一轮弹回 120 条。
                if agent is not None:
                    from app.services.history.context_unlock import unlock_context
                    await unlock_context(
                        db, agent,
                        group_id=group_id,
                        session_id=context.get("session_id"),
                        conversation_type=context.get("conversation_type")
                        or ("group" if group_id else "dm"),
                        summary=stats.get("summary") or "",
                        trigger_user_id=context.get("trigger_user_id"),
                    )
                    context["_precompressed"] = True   # 本轮已经压过，别再让空闲那条路重复压
                logger.info(
                    f"AI agent_id={agent_id} 主动压缩上下文："
                    f"{stats['before_tokens']} → {stats['after_tokens']} tokens "
                    f"（压缩 {stats['compression_ratio_pct']}%）"
                )
        except Exception as e:
            logger.error(f"compress_context 工具执行失败: {e}", exc_info=True)
            return {"error": True, "message": f"压缩失败: {e}"}

        # 未压缩（无可压缩内容 / 摘要生成失败）不是"压缩完成"——不能对 AI 撒谎说压过了
        if not stats.get("compressed"):
            reason = stats.get("reason") or "无可压缩内容"
            return {
                "success": False,
                "compressed": False,
                "reason": reason,
                "before_tokens": stats.get("before_tokens", 0),
                "message": f"上下文未压缩：{reason}。如确需压缩可调小 keep_last_n 或换更长的对话",
            }

        return {
            "success": True,
            "compressed": True,
            "before_tokens": stats.get("before_tokens", 0),
            "after_tokens": stats.get("after_tokens", 0),
            "compression_ratio_pct": stats.get("compression_ratio_pct", 0),
            "compressed_count": stats.get("compressed_count", 0),
            "after_count": stats.get("after_count", 0),
            "message": (
                f"上下文压缩完成：{stats.get('before_tokens', '?')} → "
                f"{stats.get('after_tokens', '?')} tokens"
                f"（{stats.get('compression_ratio_pct', '?')}%），"
                f"保留了最近 {keep_last_n} 条消息"
            ),
        }


ToolRegistry.register(CompressContext)
