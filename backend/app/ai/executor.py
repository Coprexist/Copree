# 从 app/ai/response_worker.py 抽离 — 工具执行引擎
"""
工具执行引擎：LLM 调用 + 工具循环 + API 配置 + 额度管理。

职责：
- 执行 _tool_call_loop（AI 的核心执行循环）
- 管理 API Key 选择、速率限制、Key 错误日志
- 发送系统错误通知
- 保存对话日志
"""
import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from sqlalchemy import select
from app.config import settings
from app.database import async_session
from app.chat import chat_api
from app.utils.pure.history import tool_ledger_note
from app.utils.pure.tool_chain import heal_tool_chain

# ── 中断消息注入：AI 忙碌时，新消息不另起 executor，注入当前循环 ──
# {agent_id: [{type: "user_message", content, sender_id, sender_name, session_id}]}
_pending_interrupts: dict[int, list[dict]] = {}
# 当前正在 _tool_call_loop 中的 agent ID 集合
_active_run_agent_ids: set[int] = set()
# 全局状态锁（保护以上两个变量的并发访问）
_state_lock = asyncio.Lock()

logger = logging.getLogger(__name__)

# 速率限制：{agent_id: last_call_timestamp}
_rate_limit_tracker: dict[int, float] = {}


async def add_pending_interrupt(agent_id: int, message: dict) -> None:
    """线程安全地添加待处理的中断消息。"""
    async with _state_lock:
        _pending_interrupts.setdefault(agent_id, []).append(message)


async def drain_pending_interrupts(agent_id: int) -> list[dict]:
    """线程安全地取出并清空指定 agent 的待处理中断消息。"""
    async with _state_lock:
        return _pending_interrupts.pop(agent_id, None) or []


async def is_agent_running(agent_id: int) -> bool:
    """检查指定 agent 是否正在执行 _tool_call_loop。"""
    async with _state_lock:
        return agent_id in _active_run_agent_ids


async def mark_agent_running(agent_id: int) -> None:
    """标记 agent 为正在执行。"""
    async with _state_lock:
        _active_run_agent_ids.add(agent_id)


async def unmark_agent_running(agent_id: int) -> None:
    """取消 agent 的执行标记。"""
    async with _state_lock:
        _active_run_agent_ids.discard(agent_id)


def _get_tool_task_summary(tool_name: str, arguments: dict) -> str | None:
    """从 ToolRegistry 获取工具的中文任务摘要，找不到时返回 None。"""
    try:
        from app.tools.base import ToolRegistry
        plugin = ToolRegistry.get_plugin(tool_name)
        if plugin:
            return plugin.get_task_summary(arguments)
    except Exception:
        pass
    return None


# ============================================================
# 系统错误通知
# ============================================================

async def _send_system_error(
    db, agent, error_type: str, detail: str = "",
    conversation_type: str = "group",
    group_id: int | None = None,
    session_id: str | None = None,
) -> None:
    """发送分类系统错误通知给 AI 的 owner（走 DM）"""
    from app.models.dm import DMMessage, DMSession

    SYSTEM_USER_ID = 0  # 硬编码：迁移保证 id=0 为系统用户

    guidance = {
        "no_api_key": (
            f"⚠️ AI「{agent.name}」缺少 API Key，无法回复消息。\n\n"
            f"📌 **解决方法**（任选其一）：\n"
            f"1. 为 AI 单独配置：[AI 设置页](/agents/{agent.id}) → 完整设置 → API 提供商 → 填写 API Key\n"
            f"2. 使用全局 Key：前往 [个人设置](/settings) → API 配置 → 填写你的 API Key（所有 AI 共用）\n"
            f"3. 使用额度兑换：前往 [兑换码页面](/me) 输入兑换码获取 API 池额度\n\n"
            f"设置完成后 AI 即可正常回复。"
        ),
        "insufficient_balance": (
            f"⚠️ AI「{agent.name}」的 API 余额不足（402），无法回复消息。\n\n"
            f"📌 **解决方法**：\n"
            f"1. 前往 DeepSeek 官网充值\n"
            f"2. 前往 [个人设置](/settings) 更换全局 API Key\n"
            f"3. 前往 [AI 设置页](/agents/{agent.id}) 更换此 AI 的 Key\n"
            f"4. 或前往 [兑换码页面](/me) 输入兑换码获取额度"
        ),
        "auth_error": (
            f"⚠️ AI「{agent.name}」的 API Key 无效（401），无法回复消息。\n\n"
            f"📌 **解决方法**：\n"
            f"1. 前往 [AI 设置页](/agents/{agent.id}) → API 提供商 → 检查并更新 API Key\n"
            f"2. 或前往 [个人设置](/settings) 更新全局 API Key\n"
            f"3. 确认 API Key 未过期、未被删除"
        ),
        "all_failed": (
            f"⚠️ AI「{agent.name}」的 API 调用全部失败。\n\n"
            f"错误详情：{detail}\n\n"
            f"📌 请前往 [AI 设置页](/agents/{agent.id}) 或 [个人设置](/settings) 检查 API 配置。"
        ),
    }

    content = guidance.get(error_type, guidance["all_failed"])

    try:
        # 统一走 DM 通知：发给 AI 的 owner，不广播到群
        owner_id = agent.owner_id
        if not owner_id:
            logger.warning(f"AI {agent.name}({agent.id}) 无 owner，无法发送系统通知")
            return

        dm_session = await chat_api.get_or_create_dm_session(db, SYSTEM_USER_ID, owner_id, skip_friendship_check=True)
        dm_sid = dm_session["session_id"]  # 返回 dict；skip_friendship_check=True：系统通知不受好友关系限制（2026-08-13 修复）

        # 复用 send_dm_message：统一处理 last_message 更新/未读标记（2026-08-13 重构，不再手动复制）
        dm_msg = await chat_api.send_dm_message(
            db, dm_sid, SYSTEM_USER_ID, content,
            skip_friendship_check=True,
            message_type="system",
        )

        # WebSocket 推送
        try:
            await chat_api.broadcast_to_dm(dm_sid, {
                "type": "new_dm_message",
                "message": {
                    **dm_msg,
                    "sender_name": "系统通知",
                    "is_system": True,
                },
            })
        except Exception:
            pass
        logger.info(f"📬 系统通知已发送给 AI {agent.name}({agent.id}) 的 Owner({owner_id})")
    except Exception as e:
        logger.error(f"  发送系统通知失败: {e}")


async def _send_system_error_notification(db, agent, content: str) -> None:
    """发送自定义系统通知（降级/余额不足等），走 DM 发给 AI 的 owner"""
    from app.models.dm import DMMessage, DMSession
    SYSTEM_USER_ID = 0
    try:
        owner_id = agent.owner_id
        if not owner_id:
            return
        dm_session = await chat_api.get_or_create_dm_session(db, SYSTEM_USER_ID, owner_id, skip_friendship_check=True)
        dm_sid = dm_session["session_id"]  # 返回 dict；skip_friendship_check=True：系统通知不受好友关系限制（2026-08-13 修复）
        # 复用 send_dm_message：统一处理 last_message 更新/未读标记（2026-08-13 重构）
        dm_msg = await chat_api.send_dm_message(
            db, dm_sid, SYSTEM_USER_ID, content,
            skip_friendship_check=True,
            message_type="system",
        )
        try:
            await chat_api.broadcast_to_dm(dm_sid, {
                "type": "new_dm_message",
                "message": {
                    **dm_msg,
                    "sender_name": "系统通知",
                    "is_system": True,
                },
            })
        except Exception:
            pass
        logger.info(f"📬 自定义通知已发送给 AI {agent.name}({agent.id}) 的 Owner({owner_id})")
    except Exception as e:
        logger.error(f"  发送自定义通知失败: {e}")


# ============================================================
# API 配置获取
# ============================================================

async def _get_api_config(
    db, agent,
    exclude_pool_key_id: int | None = None,
    chatter_id: int | None = None,
    force_own_key: bool = False,
    conversation_type: str | None = None,
    excluded_sources: set[str] | None = None,
    bill_to_owner: bool = False,
) -> tuple[str | None, str, str, int | None, dict]:
    """
    获取 API Key 和 Base URL（四层优先链 + 平台赠送额度）。

    Tier 1: Agent 自有 Key
    Tier 2: 账单人有可用额度 → API Key 池
    Tier 3: 账单人自有 Key (api_credit/自配)

    excluded_sources: 已尝试失败的来源，跳过（"agent_key", "pool_key", "user_key"）

    prefer_own_key=True 时，账单人自有 Key 优先于池 Key。

    v0.1.8: chatter_id 决定账单人（通用 AI 扣聊天者，否则扣主人）。
            force_own_key=True 时跳过池 Key，直接走账单人自有 Key。
    v0.2.2: 返回 provider_info 字典，含 thinking_supported / models / base_url。
    v1.1.0: conversation_type + group_owner_pays 控制群聊账单人。
    v1.2.0: bill_to_owner — 外部通道（QQ 等）来的会话一律记在 AI 主人头上：
            对方不是平台用户，没有 Key 也没有额度，按"聊天者付费"必然解析成空。
            调用方判断"这条是不是外部通道来的"，这里只负责照办。

    返回: (api_key, api_base, credit_source, pool_key_id, provider_info)
    """
    from app.utils.crypto import decrypt_api_key, APIKeyDecryptError
    from app.models.user import User as UserModel

    api_key = None
    api_base = settings.deepseek_base_url
    credit_source = "none"
    pool_key_id = None
    provider_info = {"thinking_supported": settings.is_deepseek_api, "base_url": api_base}

    if excluded_sources is None:
        excluded_sources = set()

    # 记录解密失败（用于降级通知）
    _decrypt_failures: list[str] = []

    def _try_decrypt(source_label: str, encrypted: str) -> str | None:
        """尝试解密，失败时记录来源并返回 None"""
        try:
            return decrypt_api_key(encrypted)
        except APIKeyDecryptError as e:
            _decrypt_failures.append(source_label)
            logger.warning(f"⚠️ {source_label} 解密失败: {e}")
            return None

    # 确定账单人
    if bill_to_owner:
        bill_user_id = agent.owner_id
    elif conversation_type and conversation_type != "dm" and getattr(agent, 'group_owner_pays', True):
        bill_user_id = agent.owner_id
    elif chatter_id and agent.ai_type in ("general", "semi_general"):
        bill_user_id = chatter_id
    else:
        bill_user_id = agent.owner_id

    # 查账单用户
    user_result = await db.execute(select(UserModel).where(UserModel.id == bill_user_id))
    user = user_result.scalar_one_or_none()

    # Tier 1: Agent 自有 Key
    if "agent_key" not in excluded_sources and agent.api_key_encrypted:
        key = _try_decrypt(f"Agent「{agent.name}」自有 Key", agent.api_key_encrypted)
        if key:
            api_key = key
            api_base = agent.api_base_url or settings.deepseek_base_url
            credit_source = "agent_key"
            provider_info = {"thinking_supported": "deepseek.com" in api_base, "base_url": api_base}
            return api_key, api_base, credit_source, pool_key_id, provider_info

    if user is None:
        return api_key, api_base, credit_source, pool_key_id, provider_info

    prefer_own = getattr(user, 'prefer_own_key', False)

    # force_own_key: 跳过池 Key，只用用户自有 Key
    if force_own_key:
        if "user_key" not in excluded_sources and user.api_key_encrypted:
            key = _try_decrypt(f"用户「{user.username}」自有 Key", user.api_key_encrypted)
            if key:
                api_key = key
                api_base = user.api_base_url or settings.deepseek_base_url
                credit_source = "user_key"
                provider_info = {"thinking_supported": "deepseek.com" in api_base, "base_url": api_base}
        # force_own_key 场景：解密失败 = 无法调用模型，通知用户重新配置
        if api_key is None and _decrypt_failures:
            asyncio.create_task(_notify_force_own_key_failure(db, agent, user, _decrypt_failures))
        return api_key, api_base, credit_source, pool_key_id, provider_info

    effective_credit = max(0, (user.platform_gifted_credit or 0)) + (user.api_credit or 0)

    # 定义 tier 顺序（prefer_own 交换 pool_key 和 user_key 的优先级）
    if prefer_own:
        tier_order = [("user_key", user.api_key_encrypted), ("pool_key", effective_credit > 0)]
    else:
        tier_order = [("pool_key", effective_credit > 0), ("user_key", user.api_key_encrypted)]

    for source_name, available in tier_order:
        if source_name in excluded_sources or not available:
            continue

        if source_name == "pool_key":
            from app.services.infrastructure.quota_service import find_best_pool_key
            pool_key = await find_best_pool_key(db, user.id, exclude_pool_key_id=exclude_pool_key_id)
            if not pool_key:
                continue
            key = _try_decrypt(f"池 Key #{pool_key.id}", pool_key.api_key_encrypted)
            if key:
                api_key = key
                api_base = pool_key.api_base_url or settings.deepseek_base_url
                credit_source = "pool_key"
                pool_key_id = pool_key.id
                from app.services.infrastructure.system_settings_service import get_provider_for_pool_key
                pool_provider = await get_provider_for_pool_key(db, pool_key)
                provider_info = {
                    "thinking_supported": pool_provider.get("thinking_supported", "deepseek.com" in api_base),
                    "base_url": api_base,
                    "provider_name": pool_provider.get("name", ""),
                }
                return api_key, api_base, credit_source, pool_key_id, provider_info

        elif source_name == "user_key":
            if user.api_key_encrypted:
                key = _try_decrypt(f"用户「{user.username}」自有 Key", user.api_key_encrypted)
                if key:
                    api_key = key
                    api_base = user.api_base_url or settings.deepseek_base_url
                    credit_source = "user_key"
                    provider_info = {"thinking_supported": "deepseek.com" in api_base, "base_url": api_base}
                    return api_key, api_base, credit_source, pool_key_id, provider_info

    # 降级完成：如果发生了 Key 解密失败，通知用户（fire-and-forget，不阻塞回复）
    if _decrypt_failures:
        asyncio.create_task(_notify_key_degradation(db, agent, user, _decrypt_failures))

    # 以上都不可用 → 返回空
    return api_key, api_base, credit_source, pool_key_id, provider_info


async def _notify_key_degradation(db, agent, user, failures: list[str]) -> None:
    """Key 解密失败导致降级时，通知 AI 的 owner"""
    try:
        failed_list = "\n".join(f"- {f}" for f in failures)
        msg = (
            f"⚠️ API Key 解密失败，已回退到全局默认配置\n\n"
            f"**失败来源**：\n{failed_list}\n\n"
            f"📌 可能原因：系统加密密钥已变更。\n"
            f"请前往 [AI 设置页](/agents/{agent.id}) 和 [个人设置](/settings) 重新填写 API Key。"
        )
        await _send_system_error_notification(db, agent, msg)
        logger.warning(f"降级通知已发送给 AI「{agent.name}」的 Owner({agent.owner_id})，失败来源: {failures}")
    except Exception as e:
        logger.error(f"发送降级通知失败: {e}")


async def _notify_force_own_key_failure(db, agent, user, failures: list[str]) -> None:
    """force_own_key 场景：解密失败 = 无法调用模型，通知用户重新配置"""
    try:
        failed_list = "\n".join(f"- {f}" for f in failures)
        msg = (
            f"⚠️ AI「{agent.name}」无法使用自有 API Key\n\n"
            f"你开启了「强制使用自有 Key」，但以下 Key 解密失败：\n{failed_list}\n\n"
            f"📌 **AI 暂时无法回复消息。**\n"
            f"请前往 [AI 设置页](/agents/{agent.id}) 重新填写 API Key。"
        )
        await _send_system_error_notification(db, agent, msg)
        logger.warning(f"force_own_key 解密失败通知已发送给 AI「{agent.name}」的 Owner({agent.owner_id})")
    except Exception as e:
        logger.error(f"发送 force_own_key 失败通知失败: {e}")


# ============================================================
# 工具调用循环
# ============================================================

async def _tool_call_loop(
    db,
    agent,
    group_id: int | None,
    messages: list[dict],
    tools: list[dict],
    model: str,
    api_base_url: str,
    api_key: str | None,
    max_loops: int = 5,
    chain_depth: int = 0,
    conversation_type: str = "group",
    session_id: str | None = None,
    trigger_user_id: int | None = None,
    effective_cfg: dict | None = None,
    credit_source: str = "user_key",
    pool_key_id: int | None = None,
    provider_supports_thinking: bool | None = None,
    trigger: str = "user",
    is_federated: bool = False,
) -> None:
    """
    工具调用循环：LLM 必须通过工具调用来执行所有操作（包括发消息）。

    铁律：文字不能自动发出去。想说话必须调 send_gm（群聊）或 send_dm（私信）。
    
    上下文压缩规则：在调 send_gm/send_dm 发消息之前的所有操作（工具调用、记忆查询等）
    会完整保留。当你调用了 send_gm/send_dm 之后，之前的中间操作链才会被压缩清理。

    v0.1.3: trigger_user_id 传入工具上下文供 store_memory 做 per-user 隔离。
    effective_cfg 为 get_effective_config 的返回值，提供 per-user 定制的 LLM 参数。
    v0.1.5: credit_source + pool_key_id 用于 LLM 调用后额度扣除。
    v0.1.5: stream=True 流式调用 + 工具格式校验 + trigger 字段（user/auto）。
    """
    if effective_cfg is None:
        effective_cfg = {}
    from app.ai.llm import chat_completion
    from app.services.tool_registry import dispatch_tool_call

    context = {
        "api_key": api_key,
        "api_base_url": api_base_url,
        "manager": chat_api,  # v1.x: ChatApi 代替原 ConnectionManager
        "agent_name": agent.name,
        "chain_depth": chain_depth,
        "conversation_type": conversation_type,
        "session_id": session_id,
        "trigger_user_id": trigger_user_id,
        "is_federated": is_federated,
        "_messages": messages,       # compress_context 工具需要读写
        "_agent": agent,             # compress_context 需要 agent.id 做 user_id
        "_model": model,             # 压缩用的模型
    }

    # 追踪 AI 在做什么（用于中断恢复）
    last_task = None
    # 累积 token 消耗（跨多轮工具调用）
    total_usage: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "reasoning_tokens": 0, "cached_tokens": 0, "api_calls": 0}
    # system_reminder 额外轮次：AI 返回文字但忘了调 send_message 时，提醒不消耗配额
    _reminder_extra = 0
    # 上下文压缩：每轮工具调用循环最多自动压缩一次
    _auto_compressed = False
    # AI 尚未在本次循环中发过消息 → 压缩只应在空闲强制时触发，保中间结果
    _has_sent_message = False
    # 轮末封存用的工具总账（工具轮历史 → 账本）
    _tool_log: list[dict] = []

    async def _seal(reasoning: str = "") -> None:
        """三个出口（end_turn 工具 / intent=end_turn / 循环走完）共用这一次封存。"""
        try:
            await _seal_turn(
                db, agent, group_id=group_id, session_id=session_id,
                conversation_type=conversation_type, tool_log=_tool_log,
                settlement=_settlement, reasoning=reasoning,
            )
        except Exception as e:
            logger.warning(f"轮末封存失败（非致命，下一轮只少了这一笔）: {e}")

    # ── 空闲强制压缩（2026-08-13 前移）：在第一次 LLM 调用之前检查 12h 空闲。
    # 之前放在工具循环内（调用成功后），key 失效等早期失败时根本执行不到——
    # 12 天没聊的对话带着全量历史硬跑，且 key 修复前永远不压缩。前移后即使本次失败，
    # 下次重试时上下文已瘦身。
    # 优先级（2026-08-13 产品定）：API 通 → LLM 总结压缩（保留要点）；API 不通 → 内联截断兜底
    try:
        from app.services.memory.context_compression_service import (
            inline_compress, compress_messages, should_compress, get_compression_thresholds,
        )
        from app.utils.pure.model_window import context_window_for
        # 体积是「该不该压」的必要条件，空闲只决定「什么时候压最划算」（§6）：
        # 冷路径用 T_idle——没超就不压，小上下文白跑一次摘要不值
        thresholds = await get_compression_thresholds(db)
        window = context_window_for(model)  # 窗口按模型取，别拿全局常量套所有模型
        stale = (
            _is_conversation_idle(messages, hours=12)
            and should_compress(messages, context_window=window, threshold=thresholds.idle)
        )
        if stale and not getattr(context, "_precompressed", False):
            compressed_ok = False
            # 优先 LLM 总结压缩（保留关键信息，适合继续对话的场景）
            if api_key:
                try:
                    messages, compress_stats = await compress_messages(
                        messages,
                        api_base_url=api_base_url,
                        api_key=api_key,
                        model=model,
                        user_id=str(agent.id),
                    )
                    if compress_stats.get("compressed"):
                        compressed_ok = True
                        logger.info(
                            f"AI {agent.name}({agent.id}) 空闲>12h LLM 总结压缩: "
                            f"{compress_stats.get('before_tokens')}→{compress_stats.get('after_tokens')} tok"
                        )
                except Exception as e:
                    logger.warning(f"空闲 LLM 压缩失败，回退内联截断: {e}")
            # API 不可用或总结失败 → 内联截断兜底
            if not compressed_ok:
                messages, compress_stats = inline_compress(messages)
                if compress_stats.get("compressed"):
                    compressed_ok = True
                    logger.info(
                        f"AI {agent.name}({agent.id}) 空闲>12h 内联截断(API不可用): "
                        f"{compress_stats.get('before_tokens')}→{compress_stats.get('after_tokens')} tok"
                    )
            if compressed_ok:
                _auto_compressed = True
                context["_precompressed"] = True
                # 解锁点整套（以前这条路径只压内存、不重写账本 → 下一轮原文又回来了）
                await _unlock_context(
                    db, agent, group_id=group_id, session_id=session_id,
                    conversation_type=conversation_type,
                    summary=(compress_stats.get("summary") or ""),
                )
    except Exception as e:
        logger.warning(f"调用前空闲压缩跳过（非致命）: {e}")

    loop_idx = 0
    while loop_idx < max_loops + _reminder_extra:
        # ── v0.1.5: 带分类重试的 LLM 调用 ──
        from app.ai.llm import RateLimitError, ServerError, KeyFatalError
        from app.services.infrastructure.api_key_concurrency import concurrency_mgr

        MAX_SERVER_RETRIES = 2
        excluded_sources: set[str] = set()  # 已尝试失败的来源（tier 级别）
        _excluded_pool_key_id: int | None = None  # 429 限流时排除特定池 Key
        last_error_type = None  # 追踪最后一个错误类型，用于系统通知
        last_error_detail = ""
        current_api_key = api_key
        current_api_base = api_base_url
        current_credit_source = credit_source
        current_pool_key_id = pool_key_id

        response = None
        # 降级重试循环：逐 tier 尝试，KeyFatal → 排除来源 → 下一级
        for key_attempt in range(3):
            # 切换 Key 或 tier 时重新获取配置
            if key_attempt > 0:
                _prev_source = current_credit_source
                current_api_key, current_api_base, current_credit_source, current_pool_key_id, _ = \
                    await _get_api_config(db, agent, excluded_sources=excluded_sources,
                                          exclude_pool_key_id=_excluded_pool_key_id,
                                          chatter_id=trigger_user_id)
                # 没有新 tier 可用 → 跳出
                if not current_api_key:
                    break
                # 同 tier 但没换到新 Key（非 pool_key 场景）→ 跳出
                if current_credit_source == _prev_source and current_credit_source != "pool_key":
                    break

            # 获取并发槽位
            acquired = False
            if current_pool_key_id:
                # 获取 Key 的 concurrent_limit 用于并发判断
                from app.models.api_key_pool import ApiKeyPool as ApiKeyPoolModel
                key_result = await db.execute(
                    select(ApiKeyPoolModel).where(ApiKeyPoolModel.id == current_pool_key_id)
                )
                key_row = key_result.scalar_one_or_none()
                db_limit = getattr(key_row, 'concurrent_limit', None) if key_row else None
                if not await concurrency_mgr.acquire(current_pool_key_id, model, db_limit):
                    continue  # Key 已满，换下一个
                acquired = True

            # 流式逐工具分发：回调在 SSE 解析到完整 tool_call 时即刻执行
            _pending_results: list[dict] = []  # {tc_id, result}
            _end_turn = False
            # 轮末结算（docs/dev/conversation_history.md §5）：思考留不留 + 留给后面自己的关键信息
            _settlement: dict = {}

            def _repair_json(raw: str) -> dict | None:
                """尝试修复 LLM 生成的内容字段引号嵌套问题"""
                try:
                    return json.loads(raw)
                except json.JSONDecodeError:
                    pass
                # JSON 尾部可能因内容过长被截断或引号不闭合
                # 尝试提取 path + content 两个字段
                m_path = re.search(r'"path"\s*:\s*"([^"]+)"', raw)
                if not m_path:
                    return None
                path = m_path.group(1)
                m_content = re.search(r'"content"\s*:\s*"(.+)$', raw, re.DOTALL)
                if m_content:
                    raw_content = m_content.group(1).rstrip()
                    # 去掉末尾可能残留的 , 或 }
                    raw_content = re.sub(r'"?\s*[,}]?\s*$', '', raw_content)
                    # 反转义
                    content = raw_content.replace('\\"', '"').replace('\\n', '\n').replace('\\\\', '\\')
                    return {"path": path, "content": content}
                return None

            async def _dispatch_one_tool(tc: dict):
                nonlocal last_task, _end_turn, _settlement
                if _end_turn:
                    # 同一批里前一个工具已 end_turn → 后面的不执行，但**必须**留一条 tool 响应：
                    # assistant(tool_calls) 里每个 id 都要有回应，少一条整次请求 400
                    # （2026-09-14 修的同类线上问题，兜底见 app/utils/pure/tool_chain.py）
                    _pending_results.append({"tc_id": tc.get("id", ""), "result": {
                        "success": False,
                        "error": "本轮已结束（前一个工具调用了 end_turn），该工具未执行",
                    }})
                    return
                tc_id = tc.get("id", "")
                func_info = tc.get("function", {})
                tool_name = func_info.get("name", "")
                arguments_str = func_info.get("arguments", "{}")
                try:
                    arguments = json.loads(arguments_str)
                except json.JSONDecodeError:
                    arguments = _repair_json(arguments_str)
                    if arguments is None:
                        _pending_results.append({"tc_id": tc_id, "result": {
                            "error": True, "message": f"工具 {tool_name} 参数 JSON 解析失败，且自动修复未能恢复。请尝试减少内容中引号的使用，或分多次 file_edit 写入。原始值前200字符: {arguments_str[:200]}"
                        }})
                        return
                    logger.info(f"AI {agent.name}({agent.id}): 自动修复 {tool_name} 参数 JSON 成功")
                from app.services.tool_registry import validate_tool_call
                is_valid, validate_error = validate_tool_call(tool_name, arguments)
                if not is_valid:
                    logger.warning(f"工具格式校验失败: {validate_error}")
                    _pending_results.append({"tc_id": tc_id, "result": {"error": True, "message": validate_error}})
                    return
                logger.info(f"AI {agent.name} 调用工具: {tool_name}({arguments})")
                # 消息类工具推送"正在输入中…"状态
                if tool_name in ("send_gm", "send_dm") and trigger == "user":
                    _typing_data: dict = {"user_id": agent.user_id, "agent_name": agent.name, "agent_avatar_url": agent.avatar_url, "trigger": trigger}
                    if conversation_type == "dm" and session_id:
                        _typing_data["session_id"] = session_id
                    elif group_id is not None:
                        _typing_data["group_id"] = group_id
                    _typing_event = {"type": "ai_typing", "conversation_type": conversation_type, "data": _typing_data}
                    try:
                        if conversation_type == "dm" and session_id:
                            await chat_api.broadcast_to_dm(session_id, _typing_event)
                        elif group_id is not None:
                            await chat_api.broadcast_to_group(group_id, _typing_event)
                    except Exception:
                        pass
                # 任务摘要追踪（通过 ToolRegistry 获取，找不到时回退到通用摘要）
                task_summary = _get_tool_task_summary(tool_name, arguments)
                if not task_summary:
                    task_summary = f"调用工具 {tool_name}"
                result = await dispatch_tool_call(db, agent.id, group_id, tool_name, arguments, context)
                if task_summary:
                    last_task = task_summary
                    if isinstance(result, dict):
                        result["__task"] = task_summary
                _pending_results.append({"tc_id": tc_id, "result": result})
                # 工具总账：工具名 + 失败原因或工具自报的摘要——轮末封存为一条 tool 条目
                _tool_log.append({"name": tool_name, "note": tool_ledger_note(result)})
                if isinstance(result, dict) and result.get("end_turn"):
                    _end_turn = True
                    # 收下结算决定（还没接账本：账本封存落地后在这里写条目）
                    _settlement["keep_thinking"] = bool(result.get("keep_thinking"))
                    _settlement["key_note"] = (result.get("key_note") or "").strip()
                # 追踪 AI 是否已发消息
                if tool_name in ("send_gm", "send_dm"):
                    _has_sent_message = True
                    # AI 刚发了消息→重置压缩标记，允许下一轮清理之前的操作链
                    _auto_compressed = False

            # ── 自动上下文压缩 ──
            # 策略：AI 没发消息时不压缩（保全中间操作链），发过消息后用 LLM 总结重要事件再压缩。
            # 另外 12 小时空闲时内联压缩（缓存已过期）——但要先过 T_idle 的体积门槛（§6）。
            if not _auto_compressed:
                from app.services.memory.context_compression_service import should_compress, inline_compress, compress_messages, get_compression_thresholds
                from app.utils.pure.model_window import context_window_for
                thresholds = await get_compression_thresholds(db)
                window = context_window_for(model)
                # 两条路各自的体积门槛（§6）：冷（久未活跃）用更低的 T_idle，
                # 热（本轮已发过消息）用 T_hot；都不满足就不压——体积是必要条件，空闲只决定时机
                stale = _is_conversation_idle(messages, hours=12) and should_compress(
                    messages, context_window=window, threshold=thresholds.idle)
                if stale or (_has_sent_message and should_compress(
                        messages, context_window=window, threshold=thresholds.hot)):
                    if stale:
                        # 空闲压缩：直接内联截断（缓存已过期，不浪费 API）
                        messages, compress_stats = inline_compress(messages)
                        if compress_stats.get("compressed"):
                            _auto_compressed = True
                    else:
                        # AI 刚发了消息：用 LLM 总结重要事件后再压缩，保留关键信息
                        # 压缩必须成功才能清空操作链，否则卡住重试
                        new_messages, compress_stats = await compress_messages(
                            messages,
                            api_base_url=current_api_base,
                            api_key=current_api_key,
                            model=model,
                            user_id=str(agent.id),
                        )
                        if compress_stats.get("compressed"):
                            messages = new_messages
                            _auto_compressed = True
                        else:
                            logger.warning(
                                f"AI {agent.name}({agent.id}) LLM 压缩失败"
                                f"（{compress_stats.get('reason', '未知')}），降级为内联压缩兜底"
                            )
                            # 兜底：不依赖 LLM 的内联截断——避免 LLM 超时/Key 不可用时反复重试耗 API
                            messages, inline_stats = inline_compress(messages)
                            if inline_stats.get("compressed"):
                                _auto_compressed = True
                                compress_stats = inline_stats
                    if _auto_compressed:
                        logger.info(
                            f"AI {agent.name}({agent.id}) 上下文压缩完成："
                            f"{compress_stats['before_tokens']} → {compress_stats['after_tokens']} tokens"
                        )
                        try:
                            await _unlock_context(
                                db, agent, group_id=group_id, session_id=session_id,
                                conversation_type=conversation_type,
                                summary=(compress_stats.get("summary") or ""),
                            )
                            await db.commit()
                        except Exception:
                            pass

            # ── 注入用户忙时消息（中断缓冲）──
            pending_msgs = await drain_pending_interrupts(agent.id)
            if pending_msgs:
                try:
                    for pm in pending_msgs:
                        if pm.get("type") == "user_message":
                            from zoneinfo import ZoneInfo
                            tz = ZoneInfo(settings.display_timezone)
                            now_str = datetime.now(tz).strftime(f"%Y-%m-%d %H:%M {tz.key}")
                            sender_name = pm.get("sender_name", "用户")
                            sender_id = pm.get("sender_id")
                            msg_struct = {
                                "time": now_str,
                                "speaker_name": sender_name,
                                "speaker_id": sender_id,
                                "is_self": False,
                                "content": pm.get("content", ""),
                            }
                            from app.utils.pure.prompting import format_message
                            messages.append({
                                "role": "user",
                                "content": format_message(msg_struct, agent.name, max_content_len=-1),
                            })
                    logger.info(f"AI {agent.name}({agent.id}): 注入 {len(pending_msgs)} 条中断消息")
                except Exception:
                    # 注入失败：回写缓冲，避免消息永久丢失
                    logger.warning(
                        f"AI {agent.name}({agent.id}) 中断消息注入失败，回写 {len(pending_msgs)} 条",
                        exc_info=True,
                    )
                    async with _state_lock:
                        old = _pending_interrupts.get(agent.id) or []
                        _pending_interrupts[agent.id] = pending_msgs + old

            # 发请求前先补齐工具链（并行调用被跳过 / 任何中断路径都可能留悬空 tool_calls，
            # 少一条响应整次请求就 400；纯函数校验，O(n)，代价可忽略）
            if heal_tool_chain(messages):
                logger.warning(f"🔧 AI {agent.name}({agent.id}) 补齐悬空 tool_calls（避免 400）")
            try:
                # 内层：同 Key 重试（500/503）
                for server_retry in range(MAX_SERVER_RETRIES + 1):
                    try:
                        response = await chat_completion(
                            messages=messages,
                            model=model,
                            api_base_url=current_api_base,
                            api_key=current_api_key,
                            tools=tools if tools else None,
                            temperature=effective_cfg["temperature"] or 0.8,
                            top_p=effective_cfg["top_p"] or 0.9,
                            presence_penalty=effective_cfg["presence_penalty"] or 0.5,
                            frequency_penalty=effective_cfg["frequency_penalty"] or 0.5,
                            thinking_enabled=effective_cfg["thinking_enabled"],
                            stream=True,
                            pool_key_id=current_pool_key_id,
                            provider_supports_thinking=provider_supports_thinking,
                            on_tool_call=_dispatch_one_tool,
                            agent_id=agent.id,
                            db=db,
                        )
                        # 更新池 Key ID（可能已切换）
                        pool_key_id = current_pool_key_id
                        credit_source = current_credit_source
                        api_key = current_api_key
                        api_base_url = current_api_base
                        break  # 成功
                    except ServerError as e:
                        if server_retry < MAX_SERVER_RETRIES:
                            delay = 2 if e.status_code == 500 else 3
                            logger.warning(
                                f"AI {agent.name}({agent.id}) 服务器 {e.status_code}，"
                                f"{delay}s 后同 Key 重试 ({server_retry + 1}/{MAX_SERVER_RETRIES})"
                            )
                            await asyncio.sleep(delay)
                            continue
                        else:
                            raise  # 同 Key 重试耗尽，抛出给外层

                break  # 成功，退出 Key 切换循环

            except RateLimitError as e:
                last_error_type = "rate_limited"
                last_error_detail = e.message
                if current_pool_key_id:
                    await concurrency_mgr.mark_rate_limited(current_pool_key_id)
                    _excluded_pool_key_id = current_pool_key_id
                logger.warning(
                    f"AI {agent.name}({agent.id}) Key #{current_pool_key_id} 429，"
                    f"冷却 60s，换池 Key ({key_attempt + 1}/3)"
                )
                continue

            except KeyFatalError as e:
                fatal_type = "auth_error" if e.status_code == 401 else "insufficient_balance" if e.status_code == 402 else "key_fatal"
                last_error_type = fatal_type
                last_error_detail = e.message
                await _log_key_fatal(db, current_pool_key_id, e.status_code, e.message)
                excluded_sources.add(current_credit_source)

                # ── 降级通知：当前 tier 不可用，尝试下一级 ──
                tier_name = {
                    "agent_key": "AI 自有",
                    "user_key": "你的 API",
                    "pool_key": "系统额度",
                }.get(current_credit_source, current_credit_source)

                # 检查是否还有下一级可尝试
                _next_check = await _get_api_config(
                    db, agent, excluded_sources=excluded_sources,
                    chatter_id=trigger_user_id
                )
                has_fallback = _next_check[0] is not None

                if has_fallback:
                    msg_text = (
                        f"⚠️ **{tier_name} Key 不可用**"
                        f"（{'余额不足' if e.status_code == 402 else 'API Key 无效' if e.status_code == 401 else '未知错误'}）。\n"
                        f"正在自动切换至下一优先级额度…"
                    )
                elif current_credit_source == "pool_key":
                    msg_text = (
                        f"⚠️ **系统额度暂不可用**（{'余额不足' if e.status_code == 402 else 'API Key 无效' if e.status_code == 401 else '未知错误'}）。\n"
                        f"此次不记入你的额度使用情况。请稍后重试或联系管理员。"
                    )
                elif current_credit_source == "user_key":
                    msg_text = (
                        f"⚠️ **你的 API 余额不足**，且无可用系统额度。\n"
                        f"请前往 [个人设置](/settings) 更新 API Key 或联系管理员补充额度。"
                    )
                else:
                    msg_text = (
                        f"⚠️ AI「{agent.name}」的 API 调用失败"
                        f"（{'余额不足' if e.status_code == 402 else 'API Key 无效' if e.status_code == 401 else '未知错误'}）。\n"
                        f"请检查 API 配置。"
                    )

                await _send_system_error_notification(db, agent, msg_text)
                logger.error(
                    f"AI {agent.name}({agent.id}) {current_credit_source} "
                    f"{e.status_code} 不可用，尝试降级 ({key_attempt + 1}/3)"
                )
                continue

            except ServerError as e:
                last_error_type = "server_error"
                last_error_detail = f"{e.status_code}: {e.message}"
                logger.error(
                    f"AI {agent.name}({agent.id}) Key #{current_pool_key_id} "
                    f"{e.status_code} 重试耗尽，最终失败"
                )

            finally:
                if acquired and current_pool_key_id:
                    await concurrency_mgr.release(current_pool_key_id)

        # ── 全部重试失败 ──
        if response is None:
            logger.error(f"AI {agent.name}({agent.id}) LLM 调用全部重试失败，last_error={last_error_type}")
            await _save_conversation_log_safe(
                db, agent, messages, conversation_type,
                group_id, session_id, has_output=False, model=model,
            )
            # 发送分类系统通知
            error_type = last_error_type or "all_failed"
            await _send_system_error(db, agent, error_type, last_error_detail,
                                     conversation_type, group_id, session_id)
            return

        # ── end_turn 已在流式回调中触发 → 补 assistant_msg + tool results 后退出 ──
        if _end_turn:
            assistant_msg = {"role": "assistant", "content": response.get("content")}
            if response.get("tool_calls"):
                assistant_msg["tool_calls"] = response["tool_calls"]
            if response.get("reasoning_content"):
                assistant_msg["reasoning_content"] = response["reasoning_content"]
            messages.append(assistant_msg)
            for pr in _pending_results:
                messages.append({"role": "tool", "tool_call_id": pr["tc_id"],
                                 "content": json.dumps(pr["result"], ensure_ascii=False)})
            await _seal(response.get("reasoning_content") or "")
            logger.info(
                f"AI {agent.name}({agent.id}) end_turn 流式触发，本轮结束"
                f"（结算：keep_thinking={bool(_settlement.get('keep_thinking'))}，"
                f"key_note={'有' if _settlement.get('key_note') else '无'}）"
            )
            await _save_conversation_log_safe(
                db, agent, messages, conversation_type,
                group_id, session_id, has_output=True, model=model,
                token_usage=total_usage,
            )
            return

        content = response.get("content")
        tool_calls = response.get("tool_calls")
        finish_reason = response.get("finish_reason", "stop")

        # 累积 token 消耗 + API 调用计数
        total_usage["api_calls"] += 1
        usage = response.get("usage", {})
        if usage:
            for k in ("prompt_tokens", "completion_tokens", "total_tokens", "reasoning_tokens", "cached_tokens"):
                total_usage[k] += usage.get(k, 0)

        # ── 解析 JSON intent（轻量方案：提示词引导 + 后端解析，不用 response_format）──
        parsed_intent = None
        if content:
            try:
                parsed = json.loads(content)
                if isinstance(parsed, dict) and "intent" in parsed:
                    parsed_intent = parsed["intent"]
            except (json.JSONDecodeError, TypeError):
                pass

        # ── end_turn / no_action：AI 明确表示本轮结束 ──
        if parsed_intent in ("end_turn", "no_action") and not tool_calls and not _pending_results:
            logger.info(
                f"AI {agent.name}({agent.id}) intent={parsed_intent}，本轮结束"
            )
            await _seal(response.get("reasoning_content") or "")
            if last_task:
                try:
                    from app.services.agent.workspace_service import save_current_task
                    from app.services.agent.state_stack_service import persist_last_task_as_state
                    await save_current_task(db, agent.id, last_task)
                    await persist_last_task_as_state(
                        db, agent.id, last_task, group_id,
                        context_ref=f"group:{group_id}" if group_id else "",
                    )
                except Exception:
                    pass
            await _save_conversation_log_safe(
                db, agent, messages, conversation_type,
                group_id, session_id,
                has_output=bool(content), model=model,
                token_usage=total_usage,
            )
            return

        # ── 提醒：有文字但没有工具调用（兜底机制）──
        reminder_grace = getattr(agent, 'reminder_grace', 'every_time')
        if reminder_grace == 'off':
            reminder_max = 0
        elif reminder_grace == 'once':
            reminder_max = 1
        else:  # 'every_time'
            reminder_max = 10
        if content and not tool_calls and not _pending_results and _reminder_extra < reminder_max:
            logger.info(
                f"AI {agent.name}({agent.id}) 返回了文字但无工具调用"
                f"（intent={parsed_intent or '解析失败'}），"
                f"注入提醒: {content[:80]}"
            )
            reminder_assistant_msg = {
                "role": "assistant",
                "content": content,
                "tool_calls": [{
                    "id": "system_reminder",
                    "type": "function",
                    "function": {
                        "name": "system_reminder",
                        "arguments": "{}",
                    },
                }],
            }
            if response.get("reasoning_content"):
                reminder_assistant_msg["reasoning_content"] = response["reasoning_content"]
            messages.append(reminder_assistant_msg)
            messages.append({
                "role": "tool",
                "tool_call_id": "system_reminder",
                "content": json.dumps({
                    "reminder": True,
                    "message": (
                        "你刚才返回了文字但没有调用任何工具。"
                        "文字不能自动发送——如果你想在群聊发言，请调用 send_gm 工具；私信请用 send_dm。"
                        "括号表情可以写在 send_gm/send_dm 的 content 里发出去，"
                        "但不能只返回括号文字而不调工具。"
                        "请现在就调用 send_gm/send_dm 或你需要的其他工具。"
                        "如果你决定不再继续回复，请调用 end_turn 工具来结束本轮。"
                    ),
                }, ensure_ascii=False),
            })
            if reminder_grace != 'off':
                _reminder_extra += 1
            logger.info(f"AI {agent.name}({agent.id}) system_reminder 注入"
                        f"（grace={reminder_grace}, 额外={_reminder_extra}）")
            await asyncio.sleep(0.3)
            continue

        # ── 无工具调用也没有文字 → 退出 ──
        if not tool_calls:
            if last_task:
                try:
                    from app.services.agent.workspace_service import save_current_task
                    from app.services.agent.state_stack_service import persist_last_task_as_state
                    await save_current_task(db, agent.id, last_task)
                    await persist_last_task_as_state(
                        db, agent.id, last_task, group_id,
                        context_ref=f"group:{group_id}" if group_id else "",
                    )
                except Exception:
                    pass
            await _save_conversation_log_safe(
                db, agent, messages, conversation_type,
                group_id, session_id,
                has_output=bool(content), model=model,
                token_usage=total_usage,
            )
            return

        # ── 有工具调用 → 执行 ──
        assistant_msg: dict = {"role": "assistant", "content": content}
        assistant_msg["tool_calls"] = tool_calls
        if response.get("reasoning_content"):
            assistant_msg["reasoning_content"] = response["reasoning_content"]
        messages.append(assistant_msg)

        for pr in _pending_results:
            messages.append({
                "role": "tool",
                "tool_call_id": pr["tc_id"],
                "content": json.dumps(pr["result"], ensure_ascii=False),
            })

        # ── 闹钟模式：第一轮工具执行完后注入收尾提醒 ──
        if conversation_type == "alarm":
            messages.append({
                "role": "user",
                "content": (
                    "⏰ 闹钟任务已执行。\n"
                    "- 如果任务已完成 → 停止，不要额外发言\n"
                    "- 如果情况有变 → 根据实际情况调整行动\n"
                    "- 如果有新的重要事项 → 可以接着规划执行"
                ),
            })

        # 有工具结果 → 继续循环让 LLM 看到
        if _pending_results:
            await asyncio.sleep(0.5)
            loop_idx += 1
            continue

        # LLM 未请求 tool_calls → 已完成，保存并退出
        if finish_reason != "tool_calls":
            if last_task:
                try:
                    from app.services.agent.workspace_service import save_current_task
                    from app.services.agent.state_stack_service import persist_last_task_as_state
                    await save_current_task(db, agent.id, last_task)
                    await persist_last_task_as_state(
                        db, agent.id, last_task, group_id,
                        context_ref=f"group:{group_id}" if group_id else "",
                    )
                except Exception:
                    pass
            await _save_conversation_log_safe(
                db, agent, messages, conversation_type,
                group_id, session_id,
                has_output=True, model=model,
                token_usage=total_usage,
            )
            return

        # 短暂延迟，避免过于频繁的 API 调用
        await asyncio.sleep(0.5)
        loop_idx += 1

    # 循环耗尽
    if last_task:
        try:
            from app.services.agent.workspace_service import save_current_task
            await save_current_task(db, agent.id, last_task)
        except Exception as e:
            logger.warning(f"保存当前任务失败: {e}")
    # v0.1.8: LLM 调用后扣除额度
    if total_usage["api_calls"] > 0 and total_usage["total_tokens"] > 0:
        try:
            if conversation_type == "dm" and agent.ai_type in ("general", "semi_general") and trigger_user_id:
                bill_user_id = trigger_user_id
            else:
                bill_user_id = agent.owner_id
            from app.services.infrastructure.quota_service import deduct_credit
            await deduct_credit(
                db,
                user_id=bill_user_id,
                tokens_used=total_usage["total_tokens"],
                source=credit_source,
                pool_key_id=pool_key_id,
                agent_id=agent.id,
                model=model,
            )
        except Exception as e:
            logger.warning(f"  扣除额度失败（不阻塞主流程）: {e}")

    await _seal(response.get("reasoning_content") or "")
    await _save_conversation_log_safe(
        db, agent, messages, conversation_type,
        group_id, session_id,
        has_output=True, model=model,
        token_usage=total_usage,
    )


# ============================================================
# 速率限制
# ============================================================

def _check_rate_limit(agent_id: int) -> bool:
    """
    检查速率限制（简单内存实现）。
    返回 True 表示允许调用。
    """
    now = time.monotonic()
    last_call = _rate_limit_tracker.get(agent_id, 0)
    min_interval = 1.0 / settings.rate_limit_per_second

    if now - last_call < min_interval:
        return False

    _rate_limit_tracker[agent_id] = now
    return True


# ============================================================
# Key 致命错误日志
# ============================================================

async def _log_key_fatal(db, pool_key_id: int | None, status_code: int, message: str) -> None:
    """记录 402/401 致命错误到系统日志，通知管理员"""
    if pool_key_id is None:
        return
    try:
        from app.models.api_key_pool import ApiKeyPool as ApiKeyPoolModel
        key_result = await db.execute(
            select(ApiKeyPoolModel).where(ApiKeyPoolModel.id == pool_key_id)
        )
        key_row = key_result.scalar_one_or_none()
        key_name = key_row.name if key_row else f"#{pool_key_id}"
        error_type = "余额不足" if status_code == 402 else "API Key 无效"
        logger.warning(
            f"  ⚠️ 系统通知：API Key 池「{key_name}」({pool_key_id}) 发生致命错误："
            f"{error_type} ({status_code})，请管理员检查。详情: {message[:200]}"
        )
    except Exception as e:
        logger.error(f"记录 Key 致命错误失败: {e}")


# ============================================================
# 对话日志保存
# ============================================================

async def _save_conversation_log_safe(
    db, agent, messages: list[dict],
    conversation_type: str = "group",
    group_id: int | None = None,
    session_id: str | None = None,
    has_output: bool = False,
    model: str | None = None,
    token_usage: dict | None = None,
) -> None:
    """安全保存对话日志（失败不影响主流程）"""
    try:
        from app.services.content.conversation_log_service import save_conversation_log
        await save_conversation_log(
            db,
            agent_id=agent.id,
            messages=messages,
            conversation_type=conversation_type,
            group_id=group_id,
            session_id=session_id,
            token_usage=token_usage,
            has_output=has_output,
            model=model,
            thinking_enabled=bool(agent.thinking_enabled),
        )
    except Exception as e:
        logger.warning(f"保存对话日志失败 (agent={agent.id}): {e}")


# ============================================================
# 检查对话是否闲置
# ============================================================

# 解锁 = 一整套动作，**清单即契约**（docs/dev/conversation_history.md §6：少做一样就是半解锁）。
# 做成数据而不是散在函数体里：新增动作只加一行，执行顺序也在这里；
# `_unlock_context` 按清单执行并把**实际执行的步骤**返回，测试拿它跟清单对账——
# Python 没有「你必须调过这个方法」的编译期保证，能钉住的只有断言。
# 尚未实现的两条（复位思考保留标记 / 卸载最旧的图）随第四批的图片与思考落地时加进来。
UNLOCK_STEPS = (
    "rewrite_history",        # 重写账本（摘要 + 事件 + 最近 N 条）
    "clear_note_copies",      # 便签副本（连同撤下通知）随上下文重建离场
    "apply_pending_config",   # 应用挂起的配置
    "apply_pending_changes",  # 能力版本对齐最新（工具定义 + 提示词）
)


def _context_ref(group_id, session_id, conversation_type) -> str:
    """本会话在账本里的键（群 vs 私信）——只有这里能把三个参数翻成一个键。"""
    from app.services.history.context_sync import context_ref
    return (context_ref(group_id=group_id) if conversation_type == "group"
            else context_ref(session_id=session_id))


async def _seal_turn(db, agent, *, group_id, session_id, conversation_type,
                     tool_log: list[dict], settlement: dict, reasoning: str = "") -> None:
    """**轮末封存**：把轮内的东西（工具轮历史 + 轮末结算）写进账本。

    不封存的话，AI 下一轮只看得见消息、看不见自己上一轮干了什么——工具轮历史原本只活在当轮
    messages 里，轮一结束就没了。这里是它进账本的唯一入口。
    """
    from app.services.history.context_sync import append_events
    from app.utils.pure.history import handoff_entry, thinking_entry, tools_entry

    entries = []
    if tool_log:
        entries.append(tools_entry(tool_log))
    note = (settlement.get("key_note") or "").strip()
    if note:
        entries.append(handoff_entry(note))
    if settlement.get("keep_thinking") and (reasoning or "").strip():
        entries.append(thinking_entry(reasoning))
    if not entries:
        return
    await append_events(db, agent, _context_ref(group_id, session_id, conversation_type), entries)


async def _unlock_context(db, agent, *, group_id, session_id, conversation_type,
                          summary: str) -> tuple[str, ...]:
    """解锁点（压缩成功）的一整套收尾：按 `UNLOCK_STEPS` 顺序执行，返回实际执行的步骤名。

    为什么必须重写账本：不重写的话，下一轮 build_messages 又从账本把原文端回来——
    压了等于没压，还每轮白付一次摘要调用（§0：compact / 超时压缩是唯一重写点）。
    """
    from app.services.history.context_sync import context_ref, rewrite_context
    from app.services.memory.context_compression_service import DEFAULT_KEEP_LAST_N
    ref = _context_ref(group_id, session_id, conversation_type)

    async def _rewrite_history():
        if ref:
            await rewrite_context(db, agent, ref, summary=summary, keep_last=DEFAULT_KEEP_LAST_N)

    async def _clear_note_copies():
        from app.services.agent.state_stack_service import release_active_frame_notes
        await release_active_frame_notes(db, agent.id)

    async def _apply_pending_config():
        from app.services.agent.agent_service import apply_pending_config
        await apply_pending_config(db, agent)

    async def _apply_pending_changes():
        # 前缀版本化：compact 解锁，effective 对齐最新（工具定义 + agent 提示词）
        from app.repositories.capability_repo import SQLAlchemyCapabilityRepository
        from app.services.capability_versioning import apply_pending_changes, SOURCE_PLATFORM
        await apply_pending_changes(SQLAlchemyCapabilityRepository(db), agent,
                                    [SOURCE_PLATFORM, f"agent-prompt-{agent.id}"])

    runners = {
        "rewrite_history": _rewrite_history,
        "clear_note_copies": _clear_note_copies,
        "apply_pending_config": _apply_pending_config,
        "apply_pending_changes": _apply_pending_changes,
    }
    done: list[str] = []
    for name in UNLOCK_STEPS:
        try:
            await runners[name]()   # 清单里写了却没人实现 → KeyError，当场炸而不是悄悄半解锁
        except Exception:
            # 半解锁要**响**：哪一步挂的、前面做完了什么——静默半解锁正是当初便签那次的病根
            logger.exception(f"解锁步骤 {name} 失败：已完成 {done}，整段解锁未完成（下次会重来）")
            raise
        done.append(name)
    return tuple(done)


def _is_conversation_idle(messages: list[dict], hours: int = 12) -> bool:
    """检查对话是否闲置——缓存大概率已过期，应强制压缩。

    两种判定（2026-08-13 补：对话跨度）：
    1. 最后一条消息距现在超过 N 小时（原逻辑：长时间没对话）
    2. 对话跨度超过 N 小时（首条 user 消息到最后一条，跨天堆积也触发）——
       修复场景：12 天没对话但今天刚发消息，历史堆积 138 条不压缩，直接带全量硬跑
    """
    # 消息内容里的时间戳是**北京时间**（format_time_shanghai）：以前当 UTC 裸时间比，
    # 只要"上海时刻 > 当前 UTC 时刻"（东八区，一天里大半时间成立）就被判成"未来"、
    # 走年-1 兜底 → 变成去年 → 空闲判定恒真、每轮都压缩（2026-09-25 用户实测）
    tz_cn = ZoneInfo("Asia/Shanghai")
    now = datetime.now(tz_cn)

    def _parse_ts(content: str):
        m2 = re.search(r'\[([A-Za-z_]+) (\d{2}-\d{2} \d{2}:\d{2})\]', content)
        if not m2:
            return None
        time_str = m2.group(2)
        for offset in (0, -1):
            try:
                year = now.year + offset
                msg_time = datetime.strptime(f"{year}-{time_str}", "%Y-%m-%d %H:%M").replace(tzinfo=tz_cn)
                if msg_time > now:
                    continue          # 真·未来：只可能是跨年，退一年再看
                return msg_time
            except ValueError:
                pass
        return None

    first_ts = None
    for m in messages:
        content = m.get("content", "")
        if not isinstance(content, str):
            continue
        ts = _parse_ts(content)
        if ts is not None:
            first_ts = ts
            break

    last_ts = None
    for m in reversed(messages):
        content = m.get("content", "")
        if not isinstance(content, str):
            continue
        ts = _parse_ts(content)
        if ts is not None:
            last_ts = ts
            break

    # 判定 1：最后消息距今超过 N 小时
    if last_ts is not None and (now - last_ts).total_seconds() > hours * 3600:
        return True
    # 判定 2：对话跨度超过 N 小时（历史堆积跨天也压缩）
    if first_ts is not None and last_ts is not None:
        span = (last_ts - first_ts).total_seconds()
        if span > hours * 3600:
            return True
    return False
