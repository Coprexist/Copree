# 从 services/llm_service.py 搬迁而来
"""
LLM 调用抽象层
提供通用的聊天补全（支持工具调用）、模型解析、消息构建
"""
import json
import logging
import httpx
from datetime import datetime
from zoneinfo import ZoneInfo
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import settings
from app.models.group import Group as GroupModel
from app.models.context_config import ContextConfig
from app.chat import chat_api
from app.services.memory.memory_service import recall_relevant_memories, format_memories_for_prompt
from app.utils.pure.prompting import (
    resolve_model, build_personality_segment, format_time_shanghai,
    format_message, format_context_for_ai, assemble_system_prompt,
    chronological,
)
from app.utils.multimodal import (
    build_content, image_attachments, image_note, injected_image_count,
    messages_have_images, strip_image_parts, is_vision_unsupported_error,
)

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════
# 常量
# ══════════════════════════════════════════════════════════════

LLM_REQUEST_TIMEOUT_SECONDS = 120.0  # httpx 请求超时
LLM_MAX_TOKENS_DEFAULT = 64000     # 默认最大 token 数


# ══════════════════════════════════════════════════════════════
# API 错误异常类（供上层分类重试）
# ══════════════════════════════════════════════════════════════

class RateLimitError(Exception):
    """429 速率限制 — 需换 Key 重试"""
    def __init__(self, message: str, pool_key_id: int | None = None):
        self.message = message
        self.pool_key_id = pool_key_id


class ServerError(Exception):
    """500/503 服务端临时故障 — 同 Key 等待重试"""
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message


class KeyFatalError(Exception):
    """402/401 Key 不可用 — 通知管理员，跳过此 Key 换下一个"""
    def __init__(self, status_code: int, message: str, pool_key_id: int | None = None):
        self.status_code = status_code
        self.message = message
        self.pool_key_id = pool_key_id


# ============================================================
# 分段系统提示词（6 段设计，最大化 DeepSeek prompt cache 命中）
# 固定段（所有 AI 共享，模块级常量）：
#   core_identity — 核心规则 + 工具铁律 + 深度推理
#   rules         — 对话风格、@提及、私信、状态、文件、技能段、记忆
# 变动段（每次构建时动态生成）：
#   personality   — AI 当前人格（agent.current_system_prompt）
#   tools         — 当前状态下的可用工具清单
#   current_context — 群名/ID/时间/DM状态/工作区任务
#   injected_skills — 记忆注入 + Skill 引擎注入
# ============================================================

# 提示词从 prompts/*.txt 文件加载，直接编辑 .txt 即可修改
from app.utils.pure.prompt_loader import (
    CORE_IDENTITY, PROTOCOL_CHAT, PROTOCOL_IMMERSIVE, PROTOCOL_DIGITAL_LIFE, DM_PROTOCOL,
    MULTI_SESSION, PRIVACY_RULES, CHAT_CHAIN_RULES,
)

# 按 config_profile 选择行为协议
PROTOCOL_BY_PROFILE = {
    "chat": PROTOCOL_CHAT,
    "immersive": PROTOCOL_IMMERSIVE,
    "digital_life": PROTOCOL_DIGITAL_LIFE,
}

# 段拼接顺序（固定段在前最大化缓存命中，变动段在后）
SEGMENT_ORDER = [
    "core_identity",
    "protocol",
    "personality",
    "tools",
    "injected_skills",
]


async def chat_completion(
    messages: list[dict],
    model: str,
    api_base_url: str,
    api_key: str | None = None,
    tools: list[dict] | None = None,
    temperature: float = 0.8,
    top_p: float = 0.9,
    presence_penalty: float = 0.5,
    frequency_penalty: float = 0.5,
    max_tokens: int = LLM_MAX_TOKENS_DEFAULT,
    response_format: dict | None = None,
    thinking_enabled: bool | None = None,
    user_id: str | None = None,
    stream: bool = False,
    pool_key_id: int | None = None,
    provider_supports_thinking: bool | None = None,
    on_tool_call: callable = None,
    agent_id: int | None = None,
    db=None,
) -> dict:
    """
    LLM 聊天补全（支持流式/非流式，v0.1.3 拆分）。

    agent_id + db：AI 调用计数（情感/记忆衰减的时间尺度，分状态帧计数）。

    返回 (非流式):
        {
            "content": str | None,
            "tool_calls": list | None,
            "usage": {...}
        }
    """
    import time as _time
    from app.services.infrastructure.metrics_collector import metrics
    t0 = _time.monotonic()

    async def _dispatch(msgs: list[dict]):
        """一次实际请求（流式/非流式由调用方决定）。"""
        if stream:
            return await _chat_completion_streaming(
                msgs, model, api_base_url, api_key, tools,
                temperature, top_p, presence_penalty, frequency_penalty,
                max_tokens, response_format, thinking_enabled, user_id,
                pool_key_id, provider_supports_thinking, on_tool_call,
            )
        return await _chat_completion_non_streaming(
            msgs, model, api_base_url, api_key, tools,
            temperature, top_p, presence_penalty, frequency_penalty,
            max_tokens, response_format, thinking_enabled, user_id,
            pool_key_id, provider_supports_thinking,
        )

    try:
        try:
            result = await _dispatch(messages)
        except Exception as e:
            # 模型不吃图片（纯文本模型收到 image_url 多半 400）→ 剥掉图片重试一次，
            # 并在消息里明确告诉 AI"你看不到图"，避免它编造图片内容。
            if not (messages_have_images(messages) and is_vision_unsupported_error(str(e))):
                raise
            degraded, removed = strip_image_parts(messages)
            logger.warning(f"🖼️ 当前模型似乎不支持图片，降级为纯文本重试（剥掉 {removed} 张）: {e}")
            result = await _dispatch(degraded)
            result["vision_unsupported"] = True
    except Exception:
        await metrics.record_llm_call(_time.monotonic() - t0, success=False)
        raise
    await metrics.record_llm_call(_time.monotonic() - t0, success=True)
    # AI 调用计数（分状态帧）：情感/记忆衰减的时间尺度
    if agent_id and db is not None:
        try:
            from app.services.agent.state_stack_service import bump_frame_call_count
            await bump_frame_call_count(db, agent_id)
        except Exception:
            pass
    return result


def _build_request_url_and_headers(api_base_url: str, api_key: str | None) -> tuple[str, dict]:
    """构建 LLM API 请求 URL 和 headers（流式/非流式共用）。"""
    from app.utils.pure.llm_endpoint import chat_completions_url
    url = chat_completions_url(api_base_url)
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return url, headers


def _build_chat_payload(
    messages: list[dict],
    model: str,
    temperature: float,
    top_p: float,
    max_tokens: int,
    tools: list[dict] | None = None,
    response_format: dict | None = None,
    presence_penalty: float = 0.5,
    frequency_penalty: float = 0.5,
    thinking_enabled: bool | None = None,
    user_id: str | None = None,
    provider_supports_thinking: bool | None = None,
    stream: bool = False,
) -> dict:
    """构建 LLM chat completions 请求 payload（流式/非流式共用主体）。

    thinking_enabled 三态：None=不表态、True=显式开、False=显式关（摘要等短输出调用必须显式关，
    否则思考 token 会吃满 max_tokens）。"""
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "top_p": top_p,
        "max_tokens": max_tokens,
    }
    if stream:
        payload["stream"] = True
    if presence_penalty != 0:
        payload["presence_penalty"] = presence_penalty
    if frequency_penalty != 0:
        payload["frequency_penalty"] = frequency_penalty
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    if response_format:
        payload["response_format"] = response_format
    # 思考开关是**三态**：None=不表态（交给服务端默认）、True=显式开、False=显式关。
    # 显式关不是可有可无：DeepSeek v4 默认就会思考，思考 token 与正文**抢同一个 max_tokens 预算**，
    # 摘要这类短输出调用会被思考吃满 → content 为空（2026-09-17 实测：budget=800 时
    # completion=800 / reasoning=800 / finish_reason=length / content 空）。
    _thinking_ok = provider_supports_thinking if provider_supports_thinking is not None else settings.is_deepseek_api
    if _thinking_ok and thinking_enabled is not None:
        payload["thinking"] = {"type": "enabled" if thinking_enabled else "disabled"}
    if user_id and _thinking_ok:
        payload["user_id"] = user_id
    return payload


async def _chat_completion_non_streaming(
    messages: list[dict],
    model: str,
    api_base_url: str,
    api_key: str | None = None,
    tools: list[dict] | None = None,
    temperature: float = 0.8,
    top_p: float = 0.9,
    presence_penalty: float = 0.5,
    frequency_penalty: float = 0.5,
    max_tokens: int = LLM_MAX_TOKENS_DEFAULT,
    response_format: dict | None = None,
    thinking_enabled: bool | None = None,
    user_id: str | None = None,
    pool_key_id: int | None = None,
    provider_supports_thinking: bool | None = None,
) -> dict:
    """
    非流式聊天补全 — 当前生产路径。
    """
    url, headers = _build_request_url_and_headers(api_base_url, api_key)
    payload = _build_chat_payload(
        messages=messages, model=model, temperature=temperature, top_p=top_p,
        max_tokens=max_tokens, tools=tools, response_format=response_format,
        presence_penalty=presence_penalty, frequency_penalty=frequency_penalty,
        thinking_enabled=thinking_enabled, user_id=user_id,
        provider_supports_thinking=provider_supports_thinking,
    )

    async with httpx.AsyncClient(timeout=LLM_REQUEST_TIMEOUT_SECONDS) as client:
        response = await client.post(url, json=payload, headers=headers)

        if response.status_code != 200:
            error_text = response.text[:500]
            logger.error(f"LLM API 错误 ({response.status_code}): {error_text}")
            _raise_classified_error(response.status_code, error_text, pool_key_id=pool_key_id)
            return {}  # unreachable, 但保持类型安全

        data = response.json()
        choice = data["choices"][0]
        message = choice["message"]

        usage = dict(data.get("usage", {}))
        # 提取 reasoning_tokens（DeepSeek thinking 模式）— 始终写入，缺失时 = 0
        completion_details = usage.pop("completion_tokens_details", None) or {}
        prompt_details = usage.pop("prompt_tokens_details", None) or {}
        usage["reasoning_tokens"] = completion_details.get("reasoning_tokens", 0)
        usage["cached_tokens"] = prompt_details.get("cached_tokens", 0)

        result = {
            "content": message.get("content"),
            "tool_calls": message.get("tool_calls"),
            "usage": usage,
            "finish_reason": choice.get("finish_reason", "stop"),
        }
        # DeepSeek 推理模式会返回 reasoning_content，必须传回给 API
        if message.get("reasoning_content"):
            result["reasoning_content"] = message["reasoning_content"]
        return result


async def _chat_completion_streaming(
    messages: list[dict],
    model: str,
    api_base_url: str,
    api_key: str | None = None,
    tools: list[dict] | None = None,
    temperature: float = 0.8,
    top_p: float = 0.9,
    presence_penalty: float = 0.5,
    frequency_penalty: float = 0.5,
    max_tokens: int = LLM_MAX_TOKENS_DEFAULT,
    response_format: dict | None = None,
    thinking_enabled: bool | None = None,
    user_id: str | None = None,
    pool_key_id: int | None = None,
    provider_supports_thinking: bool | None = None,
    on_tool_call: callable = None,
) -> dict:
    """
    SSE 流式聊天补全。

    使用 httpx 流式请求，逐行解析 SSE（data: {...}\n\n），
    累加 content / reasoning_content / tool_calls，最终返回与
    非流式一致的完整 dict。

    流式解析仅用于加速工具调用检测（不完整响应即可开始组装 tool_calls），
    消息内容不逐字推送前端——最终仍由 send_message 整段发送。
    """
    url, headers = _build_request_url_and_headers(api_base_url, api_key)
    payload = _build_chat_payload(
        messages=messages, model=model, temperature=temperature, top_p=top_p,
        max_tokens=max_tokens, tools=tools, response_format=response_format,
        presence_penalty=presence_penalty, frequency_penalty=frequency_penalty,
        thinking_enabled=thinking_enabled, user_id=user_id,
        provider_supports_thinking=provider_supports_thinking, stream=True,
    )

    full_content = ""
    full_reasoning = ""
    finish_reason = "stop"
    usage: dict = {}

    # 工具调用累加器（流式模式下 tool_calls 分多个 chunk 到达）
    tool_call_acc: dict[int, dict] = {}  # index → {id, name, arguments}
    dispatched: set[int] = set()  # 已通过 on_tool_call 分发的 index

    async with httpx.AsyncClient(timeout=LLM_REQUEST_TIMEOUT_SECONDS) as client:
        async with client.stream("POST", url, json=payload, headers=headers) as response:
            if response.status_code != 200:
                error_text = (await response.aread()).decode()[:500]
                logger.error(f"LLM API 错误 ({response.status_code}): {error_text}")
                _raise_classified_error(response.status_code, error_text, pool_key_id=pool_key_id)

            async for line in response.aiter_lines():
                line = line.strip()
                if not line or not line.startswith("data:"):
                    continue

                data_str = line[len("data:"):].strip()
                if data_str == "[DONE]":
                    break

                try:
                    chunk = json.loads(data_str)
                except json.JSONDecodeError:
                    logger.warning(f"SSE 解析失败: {data_str[:200]}")
                    continue

                # 提取 usage（通常只在最后一个 chunk）
                if "usage" in chunk:
                    chunk_usage = chunk["usage"]
                    if chunk_usage:
                        usage = dict(chunk_usage)
                        completion_details = usage.pop("completion_tokens_details", None) or {}
                        prompt_details = usage.pop("prompt_tokens_details", None) or {}
                        usage["reasoning_tokens"] = completion_details.get("reasoning_tokens", 0)
                        usage["cached_tokens"] = prompt_details.get("cached_tokens", 0)

                choices = chunk.get("choices", [])
                if not choices:
                    continue

                delta = choices[0].get("delta", {})
                if not delta:
                    # 可能是只含 finish_reason 的 chunk
                    if choices[0].get("finish_reason"):
                        finish_reason = choices[0]["finish_reason"]
                    continue

                # 累加文本内容
                if delta.get("content"):
                    full_content += delta["content"]

                # 累加推理内容（仅日志记录，不推送前端）
                if delta.get("reasoning_content"):
                    full_reasoning += delta["reasoning_content"]

                # 累加工具调用（增量到达）
                if delta.get("tool_calls"):
                    for tc in delta["tool_calls"]:
                        idx = tc.get("index", 0)
                        if idx not in tool_call_acc:
                            tool_call_acc[idx] = {
                                "id": tc.get("id", ""),
                                "type": "function",
                                "function": {
                                    "name": "",
                                    "arguments": "",
                                },
                            }
                        acc = tool_call_acc[idx]
                        if tc.get("id"):
                            acc["id"] = tc["id"]
                        func = tc.get("function", {})
                        if func.get("name"):
                            acc["function"]["name"] = func["name"]
                        if func.get("arguments"):
                            acc["function"]["arguments"] += func["arguments"]

                    # 检测已完整（参数 JSON 可解析）的工具调用，即刻回调分发
                    if on_tool_call:
                        for idx in sorted(tool_call_acc.keys()):
                            if idx in dispatched:
                                continue
                            acc = tool_call_acc[idx]
                            if acc["id"] and acc["function"]["name"] and acc["function"]["arguments"]:
                                try:
                                    json.loads(acc["function"]["arguments"])
                                    dispatched.add(idx)
                                except json.JSONDecodeError:
                                    continue  # 参数尚未收全，等下一个 chunk
                                # 参数收全 → 即刻分发，不等到流结束
                                await on_tool_call(dict(acc))

                # 记录 finish_reason
                if choices[0].get("finish_reason"):
                    finish_reason = choices[0]["finish_reason"]

    # 流结束：补发尚未派发的 tool_call（参数 JSON 一直未完整的兜底）
    if on_tool_call:
        for idx in sorted(tool_call_acc.keys()):
            if idx not in dispatched:
                await on_tool_call(dict(tool_call_acc[idx]))

    # 组装最终 tool_calls（按 index 排序）
    tool_calls = None
    if tool_call_acc:
        tool_calls = [
            tool_call_acc[i]
            for i in sorted(tool_call_acc.keys())
        ]

    result: dict = {
        "content": full_content if full_content else None,
        "tool_calls": tool_calls,
        "usage": usage,
        "finish_reason": finish_reason,
    }
    if full_reasoning:
        result["reasoning_content"] = full_reasoning
    return result


def _raise_classified_error(status_code: int, error_text: str, pool_key_id: int | None = None):
    """
    按状态码分类抛出对应的异常：
    - 429 → RateLimitError（换 Key 重试）
    - 500/503 → ServerError（同 Key 等待重试）
    - 402/401 → KeyFatalError（跳过此 Key）
    - 其他 → 普通 Exception
    """
    if status_code == 429:
        raise RateLimitError(error_text, pool_key_id=pool_key_id)
    elif status_code in (500, 503):
        raise ServerError(status_code, error_text)
    elif status_code in (402, 401):
        raise KeyFatalError(status_code, error_text, pool_key_id=pool_key_id)
    else:
        raise Exception(f"LLM API 错误 ({status_code}): {error_text}")


# resolve_model ——已迁移到 utils/pure/prompting.py

# ============================================================
# 系统提示词段 builder（每个段独立构建，便于缓存优化）
# ============================================================

async def _load_prompt_overrides(db) -> dict:
    """加载管理员在系统设置中自定义的系统提示词覆盖值"""
    try:
        from app.services.infrastructure.system_settings_service import get_settings
        s = await get_settings(db)
        return (s.get("system_prompt_overrides") or {}) if s else {}
    except Exception:
        return {}


async def _get_segment_order(db) -> list[str]:
    """获取系统提示词段拼接顺序（优先 DB 配置，fallback 代码默认）"""
    try:
        from app.services.infrastructure.system_settings_service import get_settings
        s = await get_settings(db)
        order = s.get("system_prompt_order") if s else None
        if order and isinstance(order, list) and len(order) == len(SEGMENT_ORDER):
            # 验证所有 key 合法
            if set(order) == set(SEGMENT_ORDER):
                return order
    except Exception:
        pass
    return list(SEGMENT_ORDER)


async def _inject_cross_state_context(db, agent, context_ref: str, messages: list[dict]) -> None:
    """原文尾巴：记下本会话最后几轮原文；把刚离开那段对话的尾巴注入一次。

    放在历史之后、当前时间之前——动态内容沉底，前缀（system + 历史）仍可缓存。
    便签不走这里：它是**投递制**，投进来就固化在会话前缀里（见 _deliver_frame_notes）。
    """
    try:
        from app.services.agent.state_stack_service import frame_turn_context
        from app.utils.pure.state_stack import frame_tail

        block = await frame_turn_context(db, agent.id, context_ref, frame_tail(messages))
        if block:
            messages.append({"role": "system", "content": block})
    except Exception as e:
        logger.warning(f"交接尾巴注入失败（非致命）: {e}")


async def _deliver_frame_notes(db, agent, context_ref: str, entries: list[dict]) -> list[dict]:
    """跨状态便签：**投递 = 落一条 note 条目**（账本里有了就不再投，幂等）；返回本轮要追加的条目。

    以前是插进 message 0 之后的前缀块——前缀动不得，撤下只能靠尾部通知"压着"它；
    现在它是账本里的**一次性事件**：投递即入历史（只追加），撤下补一条变更通知，
    解锁（compact/清空）时随账本重写离场（flags.drop_on_unlock）。
    有效期只决定"还能不能投递"；撤下不改已投出去的那条。
    """
    try:
        from app.services.agent.cross_state_note_service import sync_frame_notes
        from app.services.agent.state_stack_service import mark_frame_notes_notified
        from app.utils.pure.cross_state_note import format_note_delivery, format_retired_notes_notice
        from app.utils.pure.history import delivered_note_ids, make_entry, note_entry

        copies = await sync_frame_notes(db, agent.id, context_ref)
        delivered = delivered_note_ids(entries)
        out = [note_entry(c["id"], format_note_delivery(c))
               for c in copies if c.get("id") and c["id"] not in delivered]
        notice = format_retired_notes_notice(copies)
        if notice:
            await mark_frame_notes_notified(
                db, agent.id, {c["id"] for c in copies if c.get("retired") and not c.get("notified")})
            # 撤下通知也是"只活到解锁"（它讲的那条便签解锁时就没了）
            out.append(make_entry("notice", notice, flags={"drop_on_unlock": True}))
        return out
    except Exception as e:
        logger.warning(f"跨状态便签投递失败（非致命）: {e}")
        return []


async def _build_capability_notice(db, agent) -> str:
    """能力变更通知：增量 changelog（known 在这一步推进，与注入同事务）。无变化返回空串。"""
    try:
        from app.repositories.capability_repo import SQLAlchemyCapabilityRepository
        from app.services.capability_versioning import build_change_notice, SOURCE_PLATFORM
        from app.services.capability_versioning import memory_index_source
        notice = await build_change_notice(
            SQLAlchemyCapabilityRepository(db), agent,
            [SOURCE_PLATFORM, f"agent-prompt-{agent.id}", memory_index_source(agent.id)])
        return notice or ""
    except Exception:
        return ""


async def _inject_personality_anchor(db, agent, system_prompt: str, language: str = "zh") -> str:
    """
    人格锚点注入 — 设计文档 6.2：只读、始终在最前面、随一致性系数缩放。

    锚点存在于 personality_anchors 表时注入，不存在则原样返回。
    注入发生在所有段落拼接之后、工作区/状态栈追加之前，保证锚点位于最前。
    """
    try:
        from app.repositories.agent_repo import SQLAlchemyAgentRepository
        from app.services.brain.brain_controller import brain_controller
        from app.utils.pure.prompting import format_personality_anchor

        anchor = await brain_controller.get_personality_anchor(SQLAlchemyAgentRepository(db), agent.id)
        if not anchor:
            return system_prompt
        anchor_text = format_personality_anchor(anchor, language)
        if not anchor_text:
            return system_prompt
        return f"{anchor_text}\n\n{system_prompt}"
    except Exception as e:
        logger.warning(f"人格锚点注入失败（非致命）: {e}")
        return system_prompt


# _build_personality ——已迁移到 utils/pure/prompting.py，导入为 build_personality_segment


async def _build_tools_segment(db, agent, is_dm: bool = False) -> str:
    """tools 段：技能背包视图——按 6 段分组展示，含段描述"""
    from app.services.tool_registry import get_allowed_tools
    from app.services.skill.skill_engine import _is_delay_reply_allowed
    from app.tools.base import SKILL_SEGMENT_META, ToolRegistry

    delay_allowed = await _is_delay_reply_allowed(db, agent)
    current_tools = get_allowed_tools(
        agent.state, thinking_enabled=agent.thinking_enabled,
        delay_reply_allowed=delay_allowed,
    )
    current_tool_names = {t["function"]["name"] for t in current_tools}

    # 🎭 状态帧工具隔离：栈顶帧指定了 tools/skills 白名单时，再过滤一层
    from app.services.agent.state_stack_service import get_active_frame_tools
    frame_tools, frame_skills = await get_active_frame_tools(db, agent.id)
    frame_note = ""
    if frame_tools is not None:
        allowed = set(frame_tools)
        current_tool_names &= allowed
        frame_note = f"（当前状态帧限定工具：{', '.join(sorted(allowed))}）"
    elif frame_skills is not None:
        current_tool_names &= set(frame_skills)
        frame_note = f"（当前状态帧限定技能：{', '.join(sorted(frame_skills))}）"

    all_segments = ToolRegistry.get_segments()

    lines = [
        "## 技能背包 · 当前可用工具",
        f"你的状态：{agent.state}　可用工具：{len(current_tool_names)} 个{frame_note}",
        "",
    ]

    for seg_key, seg_info in all_segments.items():
        seg_meta = SKILL_SEGMENT_META.get(seg_key, {})
        seg_name = seg_meta.get("name", seg_key)
        seg_desc = seg_meta.get("description", "")
        all_tools_in_seg = [t["name"] for t in seg_info.get("tools", [])]
        available = [t for t in all_tools_in_seg if t in current_tool_names]
        unavailable_count = len(all_tools_in_seg) - len(available)

        if not available:
            continue  # 该段完全不可用，不展示

        suffix = f"（另有 {unavailable_count} 个不可用）" if unavailable_count > 0 else ""
        lines.append(f"📦 **{seg_name}** — {seg_desc}{suffix}")
        lines.append(f"   {', '.join(available)}")
        lines.append("")

    lines.append(
        "工具列表中不含的工具说明当前状态下不可用。如需查看全部能力（含不可用的），"
        "调用 list_available_skills。"
    )
    return "\n".join(lines)


async def _build_current_context(
    db: AsyncSession, agent, group_id: int,
    group_name: str, is_dm: bool,
    is_federated: bool = False,
) -> str:
    """current_context 段：当前时间（v0.2.1 精简——会话标题已用统一格式）"""
    tz = ZoneInfo(settings.display_timezone)
    now = datetime.now(tz)
    now_str = now.strftime(f"%Y-%m-%d %H:%M {tz.key}")
    context = f"## 当前时间\n{now_str}\n"
    if is_dm:
        context += (
            "- **重要**：当前在 DM 中，回复请用 send_dm 或 send_gm 发送内容。\n"
        )
        context += (
            "- **注意**：你的推理/思考过程对方看不见，"
            "必须调 send_dm 或 send_gm 才能把内容发出去（除非你不想发）！\n"
        )
        context += (
            "- **支持**：消息中可用 Markdown，含表格、数学公式 (\(LaTeX\))、"
            "Mermaid 图表 (\`\`\`mermaid)、任务列表、删除线等。\n"
        )
    else:
        context += (
            f"- **重要**：当前在群聊中，回复请用 send_gm 或 send_dm 发送内容。\n"
        )
        context += (
            "- **注意**：你的推理/思考过程群成员看不见，"
            "必须调 send_gm 或 send_dm 才能把内容发出去（除非你不想发）！\n"
        )
        context += (
            "- **支持**：消息中可用 Markdown，含表格、数学公式 (\(LaTeX\))、"
            "Mermaid 图表 (\`\`\`mermaid)、任务列表、删除线等。\n"
        )
    # Federation context
    if is_federated:
        context += (
            "- **联邦共享**：此群聊已启用联邦共享，你的消息将自动同步到其他 Copree 实例，"
            "其他实例的用户可能会看到并回应你的消息。\n"
        )
    return context


async def _build_injected_skills(
    db: AsyncSession, agent, group_id: int,
    query_text: str,
    api_base_url: str | None, api_key: str | None,
    trigger_user_id: int | None = None,
) -> str:
    """
    injected_skills 段：记忆注入 + Skill 引擎注入。

    这是最动态的段，每次请求都可能不同。
    记忆注入用最近消息内容作为检索查询。

    v0.1.3: trigger_user_id 用于通用/半通用 AI 的 per-user 记忆隔离。
    """
    parts: list[str] = []

    # ── 记忆注入 ──
    if query_text.strip():
        try:
            memories = await recall_relevant_memories(
                db, agent.id,
                query=query_text,
                api_base_url=api_base_url or "https://api.deepseek.com",
                api_key=api_key,
                top_k=5,
                group_id=group_id,
                user_id=trigger_user_id,
                ai_type=agent.ai_type or "resonance",
                call_count=agent.llm_call_count or 0,
            )
            if memories:
                parts.append(format_memories_for_prompt(memories))
        except Exception as e:
            logger.warning(f"记忆注入失败（非致命）: {e}")

    # ── Skill 引擎注入（预留） ──
    try:
        from app.services.skill.skill_engine import evaluate_inject_skills
        skill_prompts = await evaluate_inject_skills(db, agent, group_id)
        if skill_prompts:
            parts.append(
                "## 当前激活的思维技能\n" +
                "\n".join(f"- {p}" for p in skill_prompts)
            )
    except Exception as e:
        logger.warning(f"Skill 注入失败（非致命）: {e}")

    return "\n\n".join(parts) if parts else ""


async def _build_memory_index(db, agent) -> str:
    """记忆索引（目录树）：**走版本链冻结**，所以它能安全地待在锁定段里。

    为什么必须冻结：索引原本每轮现读 DB，AI 中途 store_memory 一次，下一轮索引文本就变 →
    整个前缀从第 0 字节起 miss。它与当轮消息无关（只看 agent.id），本来就该在锁定段。
    冻结后：改动只写新版本 + 尾部 changelog 告知，解锁（compact/清空）后才对齐最新。
    """
    try:
        from app.repositories.capability_repo import SQLAlchemyCapabilityRepository
        from app.services.capability_versioning import (
            ensure_text_source_version, get_effective_text, memory_index_source,
        )
        from app.services.memory.structured_memory_service import format_db_records_for_prompt

        cur = await format_db_records_for_prompt(db, agent.id)
        if not cur:
            return ""
        repo = SQLAlchemyCapabilityRepository(db)
        source = memory_index_source(agent.id)
        await ensure_text_source_version(repo, source, cur, f"AI{agent.id}记忆索引")
        return await get_effective_text(repo, agent, source, cur)
    except Exception as e:
        logger.warning(f"记忆索引注入失败（非致命）: {e}")
        return ""


def _attach_image_to_message(
    messages: list[dict],
    index: int | None,
    orm_message,
    data_dir: str,
) -> int:
    """把 messages[index] 升级成多模态内容（仅当该条消息带图片附件时）。

    只让"最新一条用户消息"携带真实图片字节，历史消息保持纯文本——既护住
    prompt cache，也避免 token 爆炸。返回是否真的注入了图片。

    返回实际注入的图片数（0 = 没注入）。

    之前这里靠"格式化后的 content 里包含 ORM 原文"来反查是哪条消息，
    内容雷同时会匹配错；现在由调用方直接给出下标与 ORM 对象。
    """
    if index is None or orm_message is None:
        return 0
    attachments = getattr(orm_message, "attachments", None)
    if not image_attachments(attachments):
        return 0
    content = build_content(messages[index].get("content"), attachments, data_dir)
    if not isinstance(content, list):
        return 0  # 图片一张都没读出来 → 保持纯文本
    messages[index]["content"] = content
    return injected_image_count(content)


# _format_time ——已迁移到 utils/pure/prompting.py，导入为 format_time_shanghai
# format_message ——已迁移到 utils/pure/prompting.py
# format_context_for_ai ——已迁移到 utils/pure/prompting.py


async def _build_cross_conversation_context(
    db: AsyncSession,
    agent,
    current_group_id: int | None = None,
    current_session_id: str | None = None,
    trigger_user_id: int | None = None,
) -> list[dict]:
    """
    跨对话多会话上下文收集（已禁用）。

    v0.2.1 起永久返回空列表：原设计将其他群的消息注入为 system 上下文，
    导致 LLM 跨群回复（"历史重播"死循环）。新的「状态栈系统」已通过状态栈摘要
    提供跨任务上下文感知，不再需要原文注入。
    保留函数签名以兼容调用方（调用方均有 if cross_msgs 保护）。
    """
    return []


async def _versioned_agent_prompt(db: AsyncSession, agent, system_prompt_override: str | None) -> str | None:
    """前缀文本版本化：personality 源 = agent-prompt-{id}。

    用户/管理员改提示词 → 写新版本（ensure_text_source_version 哈希对比）；
    锁定态取 effective 快照（前缀缓存稳定，变更只尾部 changelog 告知）；
    compact/clear 解锁后生效。per-user 覆盖（system_prompt_override）不走版本化——
    那是用户级配置不是 agent 本体，保持直接生效。
    """
    if system_prompt_override:
        return system_prompt_override
    cur = getattr(agent, "current_system_prompt", None) or ""
    source = f"agent-prompt-{agent.id}"
    from app.repositories.capability_repo import SQLAlchemyCapabilityRepository
    from app.services.capability_versioning import (
        ensure_text_source_version, get_effective_text,
    )
    await ensure_text_source_version(SQLAlchemyCapabilityRepository(db), source, cur, f"AI{agent.id}提示词")
    return await get_effective_text(SQLAlchemyCapabilityRepository(db), agent, source, cur)









async def build_messages(
    db: AsyncSession,
    agent,
    group_id: int,
    limit: int = 5000,
    vector_accelerated: bool = False,
    api_base_url: str | None = None,
    api_key: str | None = None,
    trigger_user_id: int | None = None,
    system_prompt_override: str | None = None,
    context_config: ContextConfig | None = None,
) -> list[dict]:
    """
    构建发送给 LLM 的消息列表（6 段系统提示词 + 历史消息）。

    六段结构（固定段在前以最大化 prompt cache 命中）：
    1. core_identity   — 核心规则 + 工具铁律 + 深度推理
    2. personality     — AI 当前人格
    3. rules           — 对话风格、@提及、私信、状态、文件、记忆
    4. tools           — 当前可用工具清单
    5. current_context — 群名/ID/时间/DM状态/工作区
    6. injected_skills — 记忆注入 + Skill 引擎注入

    v0.1.3: system_prompt_override 用于通用/半通用 AI 的 per-user 人格覆盖。
    v2.0: context_config 支持声明式配置驱动上下文构建。
    """
    from app.services.memory.context_config_parser import context_config_parser

    if context_config is None:
        context_config = await context_config_parser.load_config_from_db(db, agent.id)

    # ── 并行获取所有上下文 ──
    # 1. 解析 DM 状态和群名
    is_dm = False
    group_name = f"群聊#{group_id}"
    try:
        group_result = await db.execute(
            select(GroupModel).where(GroupModel.id == group_id)
        )
        group_obj = group_result.scalar_one_or_none()
        if group_obj:
            group_name = group_obj.name
            is_dm = group_obj.name.startswith("DM:")
    except Exception:
        pass

    # 2. 获取最近消息（用于记忆检索 + 历史消息）
    recent_for_query = await chat_api.get_gm_messages(db, group_id, limit=5)
    query_parts: list[str] = []
    for m in recent_for_query:
        if m.content:
            query_parts.append(m.content[:200])
    speaker_names = await chat_api.resolve_speaker_names(db, recent_for_query)
    if speaker_names:
        query_parts.append("涉及用户: " + " ".join(sorted(set(speaker_names.values()))))
    query_text = " ".join(query_parts)

    # ── 获取用户语言偏好 ──
    language = "zh"
    try:
        from app.models.user import User as UserModel
        owner_result = await db.execute(select(UserModel).where(UserModel.id == agent.owner_id))
        owner = owner_result.scalar_one_or_none()
        if owner and owner.language:
            language = owner.language
    except Exception:
        pass

    # ── 加载管理员自定义的系统提示词覆盖 ──
    overrides = await _load_prompt_overrides(db)

    # ── 按 config_profile 选择行为协议（层级化加载）──
    profile = getattr(agent, 'config_profile', 'chat') or 'chat'
    protocol = PROTOCOL_BY_PROFILE.get(profile, PROTOCOL_CHAT)
    # 共振型 AI 追加「多个会话」说明（与具体 profile 无关）
    ai_type = getattr(agent, 'ai_type', 'resonance') or 'resonance'
    if ai_type not in ('general', 'semi_general'):
        protocol += MULTI_SESSION
        protocol += PRIVACY_RULES
        protocol += CHAT_CHAIN_RULES

    # ── 构建六段（应用管理员覆盖 + 配置驱动）──
    enabled_segments = context_config_parser.get_enabled_segments(context_config)

    # 前缀文本版本化：personality（用户可改提示词）走 agent-prompt-{id} 源
    # 用户/管理员改提示词 → 写新版本 + 尾部 changelog 告知，不碰前缀；compact 后生效
    eff_personality = await _versioned_agent_prompt(db, agent, system_prompt_override)

    segments = {}
    if "core_identity" in enabled_segments:
        segments["core_identity"] = overrides.get("core_identity") or CORE_IDENTITY
    
    if "personality" in enabled_segments:
        segments["personality"] = build_personality_segment(agent, language, eff_personality)
    
    if "protocol" in enabled_segments:
        segments["protocol"] = overrides.get(f"protocol_{profile}") or protocol
    
    if "tools" in enabled_segments:
        segments["tools"] = await _build_tools_segment(db, agent, is_dm)
    
    # injected_skills（记忆 + 技能注入）**不进 message 0**：它的检索词就是「最近 5 条消息」，
    # 每条新消息都让它变——它一变，整个前缀从第 0 字节起全 miss（实测：插一条消息前后首差下标 = 0）。
    # 按 §3 的规矩它就是「当轮事实」，跟状态栈/任务一起沉到尾部读数。
    dynamic_readings: list[str] = []
    if "injected_skills" in enabled_segments and context_config_parser.should_inject_skills(context_config):
        # 索引（冻结）进锁定段；召回 + 技能注入（检索词是当轮消息）进尾部读数
        segments["injected_skills"] = await _build_memory_index(db, agent)
        _memory_block = await _build_injected_skills(
            db, agent, group_id, query_text, api_base_url, api_key, trigger_user_id)
        if _memory_block:
            dynamic_readings.append(_memory_block)

    order = context_config_parser.parse_segment_order(context_config)
    system_prompt = assemble_system_prompt(segments, order)

    # ✨ 人格锚点注入（只读，始终在最前面 — 设计文档 6.2）
    system_prompt = await _inject_personality_anchor(db, agent, system_prompt, language)

    # 动态内容一律沉到尾部（message 0 只留静态段）：状态栈每次切会话都在变、任务/通道规矩/
    # 好友申请也会变——写进前缀等于每轮重建整个前缀，缓存全废。它们按「当轮事实」跟在历史后面。
    # 记忆单独提前（见下）；其余动态块（任务/状态/通道）仍沉在末尾
    tail_blocks: list[str] = []

    # ✨ 工作区任务（配置驱动）
    if context_config_parser.should_inject_workspace(context_config):
        try:
            from app.services.agent.workspace_service import get_current_task_text
            task_text = await get_current_task_text(db, agent.id)
            if task_text:
                tail_blocks.append(task_text)
        except Exception as e:
            logger.warning(f"工作区上下文注入失败（非致命）: {e}")

    # ✨ 状态栈摘要（配置驱动）
    if context_config_parser.should_inject_state_stack(context_config):
        try:
            from app.services.agent.state_stack_service import get_state_stack_summary
            stack_summary = await get_state_stack_summary(db, agent.id)
            if stack_summary:
                tail_blocks.append(stack_summary)
        except Exception as e:
            logger.warning(f"状态栈摘要注入失败（非致命）: {e}")

    # 📡 外部通道的规矩（这个群接没接 QQ、接了哪条）：让 AI 知道"我在群里只能被动回复"
    if group_id:
        try:
            from app.services.plugin import channel as channel_service
            brief = await channel_service.group_brief(db, group_id)
            if brief:
                tail_blocks.append(brief)
        except Exception as e:
            logger.warning(f"通道能力说明注入失败（非致命）: {e}")

    # 📨 待处理好友申请（AI 感知；动态内容沉底，缓存友好）
    if getattr(agent, "ai_type", None) in ("resonance", "general", "semi_general"):
        try:
            from app.services.social.friend_service import get_pending_friend_requests_for_ai
            from app.repositories.friend_repo import SQLAlchemyFriendRepository
            reqs = await get_pending_friend_requests_for_ai(
                friend_repo=SQLAlchemyFriendRepository(db), agent_user_id=agent.user_id,
            )
            if reqs:
                lines = ["\n\n## 📨 待处理好友申请（有人申请加你为好友）"]
                for r in reqs:
                    lines.append(f"- 来自「{r['name']}」：{r['message']}（申请 id: {r['id']}，可调 handle_friend_request 处理）")
                lines.append("你可以回复对方，或视情况处理（是否通过由你与用户沟通后决定）。")
                tail_blocks.append("\n".join(lines))
        except Exception as e:
            logger.warning(f"好友申请注入失败（非致命）: {e}")

    messages = [{"role": "system", "content": system_prompt}]

    # 记忆紧跟系统提示、排在对话历史之前：让 AI 先想起这个人，再读他说的话。
    # 它仍在 message 0 之外的"当轮区域"，所以前缀缓存不受影响。
    for block in dynamic_readings:
        messages.append({"role": "system", "content": block})

    # ── 多会话上下文（配置驱动）──
    if context_config_parser.should_inject_cross_conversation(context_config):
        cross_msgs = await _build_cross_conversation_context(
            db, agent, current_group_id=group_id, trigger_user_id=trigger_user_id,
        )
        if cross_msgs:
            messages.extend(cross_msgs)
            logger.info(f"  AI {agent.name}: 加入 {len(cross_msgs)} 条多会话上下文")

    # ── 当前群聊（最后一个会话标题，位置即语义）──
    messages.append({"role": "system", "content": f"在群聊「{group_name}」(id={group_id})中："})

    # ── 获取 AI 的 last_read_at（取未读消息用）──
    last_read_at = None
    try:
        from app.models.group import GroupMember
        gm_result = await db.execute(
            select(GroupMember).where(
                GroupMember.group_id == group_id,
                GroupMember.member_type == "ai",
                GroupMember.member_id == (agent.user_id or 0),
            )
        )
        gm = gm_result.scalar_one_or_none()
        if gm:
            last_read_at = gm.last_read_at
    except Exception:
        pass

    # ── 历史消息 ──
    if vector_accelerated:
        try:
            from app.services.memory.vector_pipeline import hybrid_search
            recent = await chat_api.get_gm_messages(db, group_id, limit=5, after_time=last_read_at)
            query_text_v = " ".join([m.content[:100] for m in recent])
            relevant = await hybrid_search(db, group_id, query_text_v, top_k=limit)
            for r in reversed(relevant):
                messages.append({
                    "role": role,
                    "content": f"[历史消息] {r.get('sender_name') or '未知'}: {r.get('content', '')}",
                })
        except Exception as e:
            logger.warning(f"向量检索失败，回退到最近消息: {e}")
            vector_accelerated = False

    if not vector_accelerated:
        msg_window = context_config_parser.get_message_window_config(context_config)
        max_unread = msg_window["max_unread_messages"]
        min_unread = msg_window["min_unread_messages"]
        
        from app.models.message import Message as MessageModel
        from app.services.history.context_sync import append_events, context_ref, sync_group_history
        from app.utils.pure.history import FOLD_LIMIT, ROLE_BY_ACTOR, latest_message_ref, make_entry

        # 群设置优先；没设过就用折叠默认值。0 是"不折叠"，不能当假值兜掉
        max_len = getattr(group_obj, "max_msg_display_len", None) if group_obj else None
        if max_len is None:
            max_len = FOLD_LIMIT

        # ── 历史消息：账本（只追加 + 缺口）──
        # 旧实现每轮按「最新 N 条」重建窗口，前缀每轮前移 → 整段 miss；
        # 现在渲染即落库（条目 content 就是发给模型的最终字节），新消息只往后追加。
        ledger = await sync_group_history(db, agent, group_id, cap=max_unread, max_len=max_len)
        group_ref = context_ref(group_id=group_id)  # 会话键只在这里拼一次

        # 一次性事件（能力变更通知 / 便签撤下）**落成条目**：它们属于「外界带来了什么」，
        # 必须紧跟历史——落在尾部读数之前，否则下一轮它们会从末尾跑到中间（顺序变 = 断缓存）。
        # 便签投递（逐条条目、幂等）+ 撤下通知：投过没有以账本为准
        events: list[dict] = await _deliver_frame_notes(db, agent, group_ref, ledger)
        cap_notice = await _build_capability_notice(db, agent)
        if cap_notice:
            events.append(make_entry("notice", cap_notice,
                                 flags={"drop_on_unlock": True}))
        ledger = ledger + await append_events(db, agent, group_ref, events)

        last_user_idx = None
        last_user_orm = None
        for entry in ledger:
            messages.append({
                "role": ROLE_BY_ACTOR.get(entry["actor"], "system"),
                "content": entry["content"],
            })
            if entry["actor"] == "user":
                last_user_idx = len(messages) - 1
                last_user_orm = (await db.get(MessageModel, int(entry["ref"]))) if entry.get("ref") else None

        if context_config_parser.should_inject_image(context_config):
            _n_img = _attach_image_to_message(messages, last_user_idx, last_user_orm, settings.data_dir)
            if _n_img:
                messages.append({"role": "system", "content": image_note(_n_img)})

        # 兜底（2026-09-25 漂移事故）：上下文最后一条不该落后于群内最新一条。
        # 真落后了（窗口/压缩吃掉了新消息）就至少把"还有几条没看"说清楚，
        # 别让 AI 对着旧消息作答——那正是用户看到的"答上一条"。
        try:
            from sqlalchemy import func as sa_func

            from app.models.message import Message as MessageModel

            newest_id = (await db.execute(
                select(sa_func.max(MessageModel.id)).where(MessageModel.group_id == group_id)
            )).scalar()
            # 账本水位就是「上下文里最后一条真实消息」（别再读 recent_messages：它已不存在）
            last_seen_id = latest_message_ref(ledger)
            if newest_id and int(newest_id) > last_seen_id:
                missing = (await db.execute(
                    select(sa_func.count(MessageModel.id)).where(
                        MessageModel.group_id == group_id, MessageModel.id > last_seen_id
                    )
                )).scalar() or 0
                logger.warning(
                    f"群 {group_id} 的上下文最新消息 id={last_seen_id} 落后于群内最新 id={newest_id}，"
                    f"有 {missing} 条没进上下文"
                )
                messages.append({
                    "role": "system",
                    "content": (
                        f"（注意：这个群还有 {missing} 条更新的消息没有进入你的上下文。"
                        "不要凭上面的历史猜着回答——可以用 read_conversation 翻原文，"
                        "或直接说明你只看到较早的消息。）"
                    ),
                })
        except Exception as e:
            logger.warning(f"未读兜底检查失败（非致命）: {e}")

    # 更新 AI 的最后阅读时间
    if last_read_at is not None:
        await chat_api.update_last_read(db, group_id, "ai", agent.user_id or 0)

    # ── 注入上一轮工具调用中的错误记录（同 DM 逻辑） ──
    try:
        from app.models.conversation_log import ConversationLog as ConvLog
        import json as _json
        last_log = await db.execute(
            select(ConvLog)
            .where(ConvLog.agent_id == agent.id, ConvLog.group_id == group_id, ConvLog.conversation_type == "group")
            .order_by(ConvLog.created_at.desc())
            .limit(1)
        )
        log_entry = last_log.scalar_one_or_none()
        if log_entry and log_entry.messages:
            tc_id_to_name = {}
            for m in log_entry.messages:
                if m.get("role") == "assistant" and m.get("tool_calls"):
                    for tc in m["tool_calls"]:
                        tc_id_to_name[tc["id"]] = tc.get("function", {}).get("name", "?")
            errors = []
            for m in log_entry.messages:
                if m.get("role") == "tool":
                    result = _json.loads(m.get("content", "{}")) if isinstance(m.get("content"), str) else m.get("content", {})
                    if result.get("error"):
                        tc_name = tc_id_to_name.get(m.get("tool_call_id", ""), "?")
                        err_msg = result.get("message", "")[:120]
                        errors.append(f"- {tc_name}: {err_msg}")
            if errors:
                messages.append({
                    "role": "system",
                    "content": "## 上一轮工具调用失败记录\n以下工具在上一轮调用中返回了错误，请参考修复：\n" + "\n".join(errors),
                })
    except Exception as e:
        logger.warning(f"注入工具错误记录失败（非致命）: {e}")

    # 尾部动态块（顺序即语义：先「我该干什么」，再「这里的规矩」，与上一段对话的尾巴分开放）
    for block in tail_blocks:
        messages.append({"role": "system", "content": block})

    # 撤下的便签 / 能力变更通知都在上面落成账本条目了——**这里不再当轮 append**（说完就没了正是要修的）

    # 📎 上一段对话的原文尾巴（切会话时的临时交接，只出现一轮）
    await _inject_cross_state_context(db, agent, f"group:{group_id}", messages)

    # 当前时间放在最后（每次变化，放末尾不影响前缀cache）
    current_ctx = await _build_current_context(db, agent, group_id, group_name, is_dm)
    messages.append({"role": "system", "content": current_ctx})

    # 绑定世界（群绑定 + agent 直接绑定）→ 能力清单 + 世界侧 skill 变更通知（动态尾部，缓存友好）
    if group_id or True:
        try:
            from app.models.world import World, WorldBinding
            from app.services.world.world_service import find_worlds_by_entity
            # v0.3.4: 世界绑定 AI 统一存 user_id
            bound = await find_worlds_by_entity(db, "agent", agent.user_id or 0)
            if group_id:
                bound += await find_worlds_by_entity(db, "group", group_id)
            # 绑定类型（分层注入）：skill.types 声明了该类型才注入（群绑定的类型 + agent 绑定的类型）
            # v0.3.4: agent 绑定存 user_id（entity_id 查询必须用 user_id，不能用 agent.id）
            type_slugs: set[str] = set()
            bind_rows = (await db.execute(
                select(WorldBinding).where(
                    WorldBinding.world_id.in_([w.id for w in bound]),
                    WorldBinding.entity_type.in_(("agent", "group")),
                    WorldBinding.entity_id.in_([agent.user_id or 0] + ([group_id] if group_id else [])),
                )
            )).scalars().all()
            for br in bind_rows:
                if br.group_type_slug:
                    type_slugs.add(br.group_type_slug)
            from app.services.world.world_skill_runtime import build_world_tools, build_world_tools_for_type
            seen = set()
            # 同名技能跨世界统计（清单注明：可用 world_id 参数指定执行哪个世界的版本）
            name_worlds: dict[str, list[int]] = {}
            for w in bound:
                if w.id in seen:
                    continue
                seen.add(w.id)
                for t in build_world_tools(w.id):
                    nm = ((t or {}).get("function") or {}).get("name")
                    if nm:
                        name_worlds.setdefault(nm, []).append(w.id)
            dup_hint = {nm: wids for nm, wids in name_worlds.items() if len(wids) > 1}
            seen = set()
            for w in bound:
                if w.id in seen:
                    continue
                seen.add(w.id)
                # 世界侧 skills 能力清单（按绑定类型分层注入：types 匹配的 + 通用才给）
                # 多类型绑定 → 取并集（任一类型可用就注入）；未绑定类型 → 只给通用 skill
                type_filter = sorted(type_slugs) if type_slugs else None
                skill_names = []
                if type_filter:
                    for ts in type_filter:
                        for t in build_world_tools_for_type(w.id, ts):
                            nm = ((t or {}).get("function") or {}).get("name")
                            if nm and nm not in skill_names:
                                skill_names.append(nm)
                else:
                    skill_names = [t["function"]["name"] for t in build_world_tools_for_type(w.id, None)]
                dup_note = ""
                if dup_hint:
                    parts = [f"{nm}（世界 {'/'.join(map(str, wids))} 都有，调用时可用 world_id 参数指定目标世界）" for nm, wids in dup_hint.items()]
                    dup_note = f"；注意：{'；'.join(parts)}"
                wc_line = "；另有 world_command 可发文本命令（由世界程序解析，如 旅人移动到 2,3 / 我去 2,3 / 身份 签到）" if skill_names else "。可用 world_command 发送文本命令（由世界程序解析，如 旅人移动到 2,3 / 我去 2,3 / 身份 签到）"
                messages.append({"role": "system", "content":
                    f"【本群世界】本群绑定世界「{w.name}」（#{w.id}）。"
                    + (f"你已获得世界颁布的技能工具，像调普通工具一样直接 function calling 调用：{'、'.join(skill_names)}" if skill_names else "")
                    + dup_note
                    + wc_line
                    + "。命令会以你的名义出现在群里，可见可审计。"})
                # 世界源能力变更通知（版本化懒加载：增量 changelog，known 更新同轮）
                from app.repositories.capability_repo import SQLAlchemyCapabilityRepository
                from app.services.capability_versioning import build_change_notice
                notice = await build_change_notice(SQLAlchemyCapabilityRepository(db), agent, [f"world-{w.id}"])
                if notice:
                    messages.append({"role": "system", "content": notice})
                    await db.commit()
        except Exception:
            pass
    return messages


async def build_dm_messages(
    db: AsyncSession,
    agent,
    session_id: str,
    limit: int = 5000,
    api_base_url: str | None = None,
    api_key: str | None = None,
    trigger_user_id: int | None = None,
    system_prompt_override: str | None = None,
) -> list[dict]:
    """构建 DM 私信的消息列表（6 段系统提示词 + DM 历史消息）"""
    from app.models.dm import DMMessage, DMSession
    from app.models.user import User
    from sqlalchemy import select as sa_select

    partner_name = "对方"
    # ── 获取对方信息 ──
    partner_user_id = None
    try:
        dm_sess_result = await db.execute(
            sa_select(DMSession).where(DMSession.session_id == session_id)
        )
        dm_sess = dm_sess_result.scalar_one_or_none()
        if dm_sess and agent.user_id:
            partner_user_id = dm_sess.user2_id if dm_sess.user1_id == agent.user_id else dm_sess.user1_id
            name_result = await db.execute(
                sa_select(User.username).where(User.id == partner_user_id)
            )
    except Exception:
        pass

    tz = ZoneInfo(settings.display_timezone)
    now = datetime.now(tz)
    now_str = now.strftime(f"%Y-%m-%d %H:%M {tz.key}")

    # ── DM 上下文段（精简：标题格式已承载会话信息，不再重复说教）──
    dm_context = (
        f"## 私信规则\n"
        f"- 回复对方用 send_dm(target_user_id={partner_user_id})，不要用 send_gm（那是群聊工具）\n"
        f"- 不要在这里测试工具或自言自语——这里只有对方能看到\n"
    )

    # ── 记忆检索查询 ──
    recent_dm = await db.execute(
        sa_select(DMMessage)
        .where(DMMessage.session_id == session_id)
        .order_by(DMMessage.created_at.desc())
        .limit(5)
    )
    recent_dm_list = recent_dm.scalars().all()
    query_text = " ".join([m.content[:200] for m in reversed(recent_dm_list) if m.content])
    if partner_name:
        query_text = f"{query_text} 涉及用户: {partner_name}"

    # ── 获取用户语言偏好 ──
    language = "zh"
    try:
        from app.models.user import User as UserModel
        owner_result = await db.execute(sa_select(UserModel).where(UserModel.id == agent.owner_id))
        owner = owner_result.scalar_one_or_none()
        if owner and owner.language:
            language = owner.language
    except Exception:
        pass

    # ── 加载管理员自定义的系统提示词覆盖 ──
    overrides = await _load_prompt_overrides(db)

    # ── 构建六段（DM 使用精简协议，应用管理员覆盖）──
    dm_protocol = overrides.get("dm_protocol") or DM_PROTOCOL
    dm_ai_type = getattr(agent, 'ai_type', 'resonance') or 'resonance'
    if dm_ai_type not in ('general', 'semi_general'):
        dm_protocol += MULTI_SESSION
        dm_protocol += PRIVACY_RULES
        dm_protocol += CHAT_CHAIN_RULES
    # 前缀文本版本化（同 build_messages）：personality 走 agent-prompt-{id} 源
    eff_personality = await _versioned_agent_prompt(db, agent, system_prompt_override)
    segments = {
        "core_identity": overrides.get("core_identity") or CORE_IDENTITY,
        "personality": build_personality_segment(agent, language, eff_personality),
        "protocol": dm_protocol,
        "tools": await _build_tools_segment(db, agent, is_dm=True),
        "injected_skills": await _build_memory_index(db, agent),  # 索引（冻结）进锁定段
    }
    # 召回 + 技能注入的检索词是「最近 5 条消息」，每轮都变 → 沉到尾部读数（§3）
    dynamic_readings: list[str] = []
    _memory_block = await _build_injected_skills(
        db, agent, group_id=0,  # group_id=0 表示非群聊上下文
        query_text=query_text,
        api_base_url=api_base_url,
        api_key=api_key,
        trigger_user_id=trigger_user_id,
    )
    if _memory_block:
        dynamic_readings.append(_memory_block)

    order = await _get_segment_order(db)
    system_prompt = assemble_system_prompt(segments, order)

    # ✨ 人格锚点注入（只读，始终在最前面 — 设计文档 6.2）
    system_prompt = await _inject_personality_anchor(db, agent, system_prompt, language)

    # 动态内容一律沉到尾部（message 0 只留静态段）：切会话时状态栈会变，写进前缀
    # 等于每轮重建前缀、缓存全废。它们按「当轮事实」跟在历史后面。
    # 记忆单独提前（见下）；其余动态块仍沉在末尾
    tail_blocks: list[str] = []

    # ✨ 工作区任务
    try:
        from app.services.agent.workspace_service import get_current_task_text
        task_text = await get_current_task_text(db, agent.id)
        if task_text:
            tail_blocks.append(task_text)
    except Exception as e:
        logger.warning(f"DM 工作区上下文注入失败（非致命）: {e}")

    # ✨ 状态栈摘要（v0.2.1）
    try:
        from app.services.agent.state_stack_service import get_state_stack_summary
        stack_summary = await get_state_stack_summary(db, agent.id)
        if stack_summary:
            tail_blocks.append(stack_summary)
    except Exception as e:
        logger.warning(f"DM 状态栈摘要注入失败（非致命）: {e}")

    # 📂 私信会话列表（v0.3.2）：AI 感知自己有哪些 DM 会话（存在性+未读数，不含内容）
    try:
        from app.models.dm import DMSession as DMSessionModel, DMMessage as DMMessageModel
        from sqlalchemy import or_ as sa_or, func as sa_func
        if agent.user_id:
            sess_res = await db.execute(
                sa_select(DMSessionModel)
                .where(sa_or(DMSessionModel.user1_id == agent.user_id, DMSessionModel.user2_id == agent.user_id))
                .order_by(DMSessionModel.last_message_at.desc().nullslast())
                .limit(8)
            )
            dm_sess_list = sess_res.scalars().all()
            if dm_sess_list:
                lines = ["\n## 📂 你的私信会话（其他会话的消息不在这里，需要时可主动 send_dm 过去）"]
                for ds in dm_sess_list:
                    if ds.session_id == session_id:
                        continue  # 当前会话单独展示
                    other_id = ds.user2_id if ds.user1_id == agent.user_id else ds.user1_id
                    oname = (await db.execute(
                        sa_select(User.username).where(User.id == other_id)
                    )).scalar_one_or_none() or f"用户{other_id}"
                    # 未读数：对方发的且自己未读
                    unread = (await db.execute(
                        sa_select(sa_func.count())
                        .select_from(DMMessageModel)
                        .where(DMMessageModel.session_id == ds.session_id, DMMessageModel.sender_id == other_id, DMMessageModel.read_at.is_(None))
                    )).scalar() or 0
                    last_t = ds.last_message_at.strftime("%m-%d %H:%M") if ds.last_message_at else "—"
                    unread_str = f"，{unread} 条未读" if unread else ""
                    lines.append(f"- 私信「{oname}」(会话 {ds.session_id})：最后消息 {last_t}{unread_str}")
                tail_blocks.append("\n".join(lines))
    except Exception as e:
        logger.warning(f"DM 会话列表注入失败（非致命）: {e}")

    # 📨 待处理好友申请（AI 感知；动态内容沉底）
    try:
        from app.services.social.friend_service import get_pending_friend_requests_for_ai
        from app.repositories.friend_repo import SQLAlchemyFriendRepository
        reqs = await get_pending_friend_requests_for_ai(
            friend_repo=SQLAlchemyFriendRepository(db), agent_user_id=agent.user_id,
        )
        if reqs:
            lines = ["\n\n## 📨 待处理好友申请（有人申请加你为好友）"]
            for r in reqs:
                lines.append(f"- 来自「{r['name']}」：{r['message']}（申请 id: {r['id']}，可调 handle_friend_request 处理）")
            lines.append("你可以回复对方，或视情况处理（是否通过由你与用户沟通后决定）。")
            tail_blocks.append("\n".join(lines))
    except Exception as e:
        logger.warning(f"DM 好友申请注入失败（非致命）: {e}")

    messages = [{"role": "system", "content": system_prompt}]

    # 记忆紧跟系统提示、排在对话历史之前：让 AI 先想起这个人，再读他说的话。
    # 它仍在 message 0 之外的"当轮区域"，所以前缀缓存不受影响。
    for block in dynamic_readings:
        messages.append({"role": "system", "content": block})

    # ── 统一上下文：数字生命档/沉浸档/共振 → 加载多会话上下文 ──
    cross_msgs = await _build_cross_conversation_context(
        db, agent, current_session_id=session_id, trigger_user_id=trigger_user_id,
    )
    if cross_msgs:
        messages.extend(cross_msgs)
        logger.info(f"  AI {agent.name}: 加入 {len(cross_msgs)} 条多会话上下文（DM）")
        # DEBUG: 打印跨对话消息的实际内容
        for i, cm in enumerate(cross_msgs):
            logger.info(f"    [{i}] role={cm['role']} content={cm['content'][:120]}...")

    # ── 当前私信（最后一个会话标题，位置即语义）──
    messages.append({"role": "system", "content": f"在私信「{partner_name}」(id={partner_user_id})中："})

    # ── DM 历史消息：账本（只追加 + 缺口）——与群聊同一套入口 ──
    from app.models.dm import DMMessage as DMMessageModel
    from app.services.history.context_sync import append_events, context_ref, sync_dm_history
    from app.utils.pure.history import ROLE_BY_ACTOR, make_entry

    ledger = await sync_dm_history(db, agent, session_id, cap=limit)
    dm_ref = context_ref(session_id=session_id)  # 会话键只在这里拼一次

    # 一次性事件（能力变更通知 / 便签撤下）落成条目：紧跟历史、排在尾部读数之前
    events: list[dict] = await _deliver_frame_notes(db, agent, dm_ref, ledger)
    cap_notice = await _build_capability_notice(db, agent)
    if cap_notice:
        events.append(make_entry("notice", cap_notice,
                                 flags={"drop_on_unlock": True}))
    ledger = ledger + await append_events(db, agent, dm_ref, events)

    last_user_idx = None
    last_user_orm = None
    for entry in ledger:
        messages.append({
            "role": ROLE_BY_ACTOR.get(entry["actor"], "system"),
            "content": entry["content"],
        })
        if entry["actor"] == "user":
            last_user_idx = len(messages) - 1
            last_user_orm = (await db.get(DMMessageModel, int(entry["ref"]))) if entry.get("ref") else None

    # 🖼️ 为最后一条用户消息注入图片附件
    _n_img = _attach_image_to_message(messages, last_user_idx, last_user_orm, settings.data_dir)
    if _n_img:
        messages.append({"role": "system", "content": image_note(_n_img)})

    # ── 注入上一轮工具调用中的错误记录 ──
    # AI 的工具调用结果存在 ConversationLog 表中，DMMessage 只存了通过 send_dm 发出去的内容。
    # 如果工具报错了，AI 可能没有把错误信息发出去，下轮上下文就丢了。
    # 这里补上最近的工具错误，让 AI 知道自己上一轮干了什么。
    try:
        from app.models.conversation_log import ConversationLog as ConvLog
        import json as _json
        last_log = await db.execute(
            sa_select(ConvLog)
            .where(ConvLog.agent_id == agent.id, ConvLog.session_id == session_id)
            .order_by(ConvLog.created_at.desc())
            .limit(1)
        )
        log_entry = last_log.scalar_one_or_none()
        if log_entry and log_entry.messages:
            # 建立 tool_call_id → tool_name 的映射
            tc_id_to_name = {}
            for m in log_entry.messages:
                if m.get("role") == "assistant" and m.get("tool_calls"):
                    for tc in m["tool_calls"]:
                        tc_id_to_name[tc["id"]] = tc.get("function", {}).get("name", "?")
            # 提取错误信息
            errors = []
            for m in log_entry.messages:
                if m.get("role") == "tool":
                    result = _json.loads(m.get("content", "{}")) if isinstance(m.get("content"), str) else m.get("content", {})
                    if result.get("error"):
                        tc_name = tc_id_to_name.get(m.get("tool_call_id", ""), "?")
                        err_msg = result.get("message", "")[:120]
                        errors.append(f"- {tc_name}: {err_msg}")
            if errors:
                messages.append({
                    "role": "system",
                    "content": "## 上一轮工具调用失败记录\n以下工具在上一轮调用中返回了错误，请参考修复：\n" + "\n".join(errors),
                })
    except Exception as e:
        logger.warning(f"注入工具错误记录失败（非致命）: {e}")

    # 能力变更通知已在上面的历史段落成账本条目了（b-2：DM 与群聊同一套）

    # 尾部动态块（顺序即语义：先「我该干什么」，再「我有哪些会话」）
    for block in tail_blocks:
        messages.append({"role": "system", "content": block})

    # 撤下的便签通知也在上面的历史段落成账本条目了

    # 📎 上一段对话的原文尾巴（切会话时的临时交接，只出现一轮）
    await _inject_cross_state_context(db, agent, session_id, messages)

    # 当前时间放在最后（每次变化，放末尾不影响前缀cache）
    dm_ctx = await _build_current_context(db, agent, 0, partner_name or "私信", is_dm=True)
    messages.append({"role": "system", "content": dm_ctx})
    return messages
