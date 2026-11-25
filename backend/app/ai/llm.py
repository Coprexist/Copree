# 从 services/llm_service.py 搬迁而来
"""
LLM 调用抽象层
提供通用的聊天补全（支持工具调用）、模型解析、消息构建
"""
import json
import logging
import re

import httpx
from datetime import datetime
from zoneinfo import ZoneInfo
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import settings
from app.ai.prefix import guard as prefix_guard, locked as prefix_locked
from app.models.group import Group as GroupModel
from app.models.context_config import ContextConfig
from app.chat import chat_api
from app.services.memory.memory_service import recall_relevant_memories
from app.utils.display_name import display_name
from app.utils.pure.chat_template import (
    ANYWHERE, fit_system_layout, is_system_layout_error,
)
from app.utils.pure.llm_endpoint import chat_completions_url, is_local_endpoint
from app.utils.pure.llm_capability import (
    is_tools_unsupported_error, remember_no_tools, supports_tools,
)
from app.utils.pure.tool_text_protocol import (
    parse_tool_calls, project_for_text_tools, render_tool_protocol,
)
from app.utils.pure.llm_usage import normalize_usage
from app.utils.pure.prompting import (
    resolve_model, build_personality_segment, format_time_shanghai,
    format_message, format_context_for_ai, assemble_system_prompt,
    chronological,
)
from app.utils.multimodal import (
    IMAGE_CHAR_HEADROOM, IMAGE_PLACEHOLDER,
    build_content, image_attachments, image_note, injected_image_count,
    messages_have_images, strip_image_parts, is_vision_unsupported_error,
    select_images_for_budget, text_attachment_blocks,
)

logger = logging.getLogger(__name__)

# 厂商说"这个 file_id 不可用"的文案特征（DeepSeek 与 Anthropic/OpenAI 的措辞都覆盖）。
# 2026-11-25：云端删过/过期/被配额回收的图，chat 请求会被整体 400 打回——不兜底就是整轮失败。
_STALE_FILE_HINT = re.compile(r"file(?:[_\s-]?(?:id|api|not[_\s-]?found|deleted|expired))?", re.I)
_STALE_FILE_REASON = re.compile(
    r"(?:expired|not[_\s-]?found|deleted|does not exist|not created under)", re.I)


def _looks_like_stale_file(detail: str) -> bool:
    """这条 400 是不是"引用的远端文件没了"。"""
    return bool(_STALE_FILE_HINT.search(detail or "") and _STALE_FILE_REASON.search(detail or ""))


def _degrade_file_blocks(messages: list[dict]) -> tuple[int, list[str]]:
    """把消息里的 {"type":"file"} 就地换成 [图片] 占位，返回 (换掉几张, 失效的 file_id)。

    降级而不是重传：这时已经在请求路径里，拿不到 db / 原图；先让这一轮发出去（AI 知道
    "这里有图但看不到"），下一轮注入时 ensure_uploaded 会重新上传。
    """
    replaced = 0
    stale: list[str] = []
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        parts: list[dict] = []
        changed = False
        for part in content:
            if isinstance(part, dict) and part.get("type") == "file":
                if part.get("file_id"):
                    stale.append(str(part["file_id"]))
                parts.append({"type": "text", "text": IMAGE_PLACEHOLDER})
                replaced += 1
                changed = True
            else:
                parts.append(part)
        if changed:
            message["content"] = parts
    return replaced, stale

# ══════════════════════════════════════════════════════════════
# 常量
# ══════════════════════════════════════════════════════════════

LLM_REQUEST_TIMEOUT_SECONDS = 120.0  # httpx 请求超时（云端）
# 本地/内网自建推理的超时。CPU 上跑 7B、prompt 几千 token，一轮两三分钟是常态：
# 2026-11-22 实测 deepseek-r1:7b 在 CPU 上回一条私信用了 181 秒，120 秒的云端口径
# 把它判成 ReadTimeout，用户看到的却是"AI 永远不回"。
LLM_LOCAL_TIMEOUT_SECONDS = 600.0
LLM_MAX_TOKENS_DEFAULT = 64000     # 默认最大 token 数


# ══════════════════════════════════════════════════════════════
# API 错误异常类（供上层分类重试）
# ══════════════════════════════════════════════════════════════

class RateLimitError(Exception):
    """429 速率限制 — 需换 Key 重试"""
    def __init__(self, message: str, pool_key_id: int | None = None):
        self.message = message
        self.pool_key_id = pool_key_id


class SystemLayoutError(Exception):
    """对端模板要求 system 必须在开头：不是网络/凭证问题，是布局结论错了。

    调用方拿 base_url 去把这条通道的结论纠正为 leading（下一轮生效）。
    """

    def __init__(self, base_url: str, message: str):
        self.base_url = base_url
        self.message = message
        super().__init__(message)


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
    CORE_IDENTITY, PLATFORM_ORIGIN, PROTOCOL_CHAT, PROTOCOL_IMMERSIVE, PROTOCOL_DIGITAL_LIFE,
    DM_PROTOCOL, MULTI_SESSION, PRIVACY_RULES, CHAT_CHAIN_RULES,
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
    system_layout: str = ANYWHERE,
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

    async def _dispatch(msgs: list[dict], tools_now: list[dict] | None, use_stream: bool):
        """一次实际请求（流式/非流式由调用方决定）。

        走文本协议时强制非流式：调用标记要等整段输出完才解析得出来，流式回调与
        增量 tool_calls 检测都拿不到完整的 `</tool_call>`。
        """
        if use_stream:
            return await _chat_completion_streaming(
                msgs, model, api_base_url, api_key, tools_now,
                temperature, top_p, presence_penalty, frequency_penalty,
                max_tokens, response_format, thinking_enabled, user_id,
                pool_key_id, provider_supports_thinking, on_tool_call,
                system_layout=system_layout,
            )
        return await _chat_completion_non_streaming(
            msgs, model, api_base_url, api_key, tools_now,
            temperature, top_p, presence_penalty, frequency_penalty,
            max_tokens, response_format, thinking_enabled, user_id,
            pool_key_id, provider_supports_thinking,
            system_layout=system_layout,
        )

    # 通道能力降级阶梯：只剥掉"对端明确说不支持"的东西，每剥一层重试一次。
    # 每一层都是同一个取舍——宁可这一轮少一项能力，也不能整轮没有回复。
    # 已记过结论的通道（10 分钟内）直接按结论发，省掉那次必然失败的往返。
    msgs = messages
    tools_now = tools
    # 文本协议：对端没有原生 function calling（tools 参数会被调度前直接拒），改把工具
    # 定义写成正文稳定块，调用与结果也用标记承载。协议文本只随工具清单变，插在 messages[0]
    # 归入稳定块，前缀缓存位置不受影响（见 pure/tool_text_protocol.py）。
    text_tools = False
    if tools_now and not supports_tools(api_base_url, model):
        logger.info(f"🔧 该通道已记为不接受原生工具调用，本轮改用文本协议: {api_base_url} / {model}")
        text_tools = True
        msgs = project_for_text_tools(msgs, render_tool_protocol(tools_now))
        tools_now = None
    marks: list[str] = []

    async def _dispatch_with_degrade() -> dict:
        nonlocal msgs, tools_now, text_tools
        while True:
            try:
                return await _dispatch(msgs, tools_now, stream and not text_tools)
            except Exception as e:
                text = str(e)
                # 1) 模型不吃图片（纯文本模型收到 image_url 多半 400）→ 剥掉图片重试，
                #    并在消息里明确告诉 AI"你看不到图"，避免它编造图片内容。
                if messages_have_images(msgs) and is_vision_unsupported_error(text):
                    msgs, removed = strip_image_parts(msgs)
                    logger.warning(f"🖼️ 当前模型似乎不支持图片，降级为纯文本重试（剥掉 {removed} 张）: {e}")
                    marks.append("vision")
                    continue
                # 2) 模型不吃 tools（本地小模型常见，如 ollama 的 deepseek-r1）→ 记下结论，
                #    本轮改走文本协议。这不是"配置错误"，是本轮请求形状要适配模型能力。
                if tools_now and is_tools_unsupported_error(text):
                    remember_no_tools(api_base_url, model)
                    text_tools = True
                    msgs = project_for_text_tools(msgs, render_tool_protocol(tools_now))
                    tools_now = None
                    logger.warning(
                        f"🔧 当前模型不接受原生工具调用，改走文本协议"
                        f"（{api_base_url} / {model}）: {e}"
                    )
                    marks.append("tools")
                    continue
                raise

    try:
        result = await _dispatch_with_degrade()
    except Exception:
        await metrics.record_llm_call(_time.monotonic() - t0, success=False)
        raise
    await metrics.record_llm_call(_time.monotonic() - t0, success=True)

    # 文本协议：把正文里的 <tool_call> 还原成 tool_calls。上层只认这一种形状——executor
    # 不用分辨通道有没有原生能力，工具执行、结果回填、收尾判定全部复用同一条链路。
    if text_tools:
        calls, rest = parse_tool_calls(result.get("content"))
        # 名字必须在这次真的给了它的工具表里：弱模型会自己起名（实测 r1 把 send_dm
        # 写成 message），照单执行只会得到"未知工具"。丢掉的调用不剥正文，让它按
        # "只说了话"走账本，比误发一条消息安全。
        known = {
            str((t.get("function") or {}).get("name") or "")
            for t in (tools or ()) if isinstance(t, dict)
        }
        calls = [c for c in calls if c["function"]["name"] in known]
        if calls:
            result["tool_calls"] = calls
            result["content"] = rest or None
        result["tool_text_protocol"] = True

    if "vision" in marks:
        result["vision_unsupported"] = True
    if "tools" in marks:
        result["tools_unsupported"] = True
    # AI 调用计数（分状态帧）：情感/记忆衰减的时间尺度
    if agent_id and db is not None:
        try:
            from app.services.agent.state_stack_service import bump_frame_call_count
            await bump_frame_call_count(db, agent_id)
        except Exception:
            pass
    return result


def _request_timeout(api_base_url: str) -> float:
    """这次请求该等多久：本机/内网自建推理给长超时，云端维持 120 秒。"""
    return LLM_LOCAL_TIMEOUT_SECONDS if is_local_endpoint(api_base_url) else LLM_REQUEST_TIMEOUT_SECONDS


def _build_request_url_and_headers(api_base_url: str, api_key: str | None) -> tuple[str, dict]:
    """构建 LLM API 请求 URL 和 headers（流式/非流式共用）。"""
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
    system_layout: str = ANYWHERE,
    # 位置放在末尾：只为判断「是不是本地端点」（决定用哪种方言关思考），
    # 不参与请求拼接。已有的位置参数调用因此不受影响。
    api_base_url: str = "",
) -> dict:
    """构建 LLM chat completions 请求 payload（流式/非流式共用主体）。

    thinking_enabled 三态：None=不表态、True=显式开、False=显式关（摘要等短输出调用必须显式关，
    否则思考 token 会吃满 max_tokens）。

    **这里是 system 摆放的唯一出口**（流式与非流式都经过它）：`system_layout` 由调用方
    从通道能力里读出来，`fit_system_layout` 按它调整。`anywhere` 原对象返回，payload
    与今天逐字节一致——DeepSeek 的前缀缓存不受这个功能影响。"""
    _is_local = is_local_endpoint(api_base_url)
    payload = {
        "model": model,
        "messages": fit_system_layout(messages, system_layout),
        "temperature": temperature,
        "top_p": top_p,
    }
    # 输出上限只在「不思考」或公网通道上发。本地通道开着思考时**不设上限**：思考与正文
    # 抢同一个额度，给个数就等于替模型决定「想多久」——想不完被截断（finish_reason=length），
    # 下一轮同样的上下文又得从头再想一遍，时间花两遍、正文却一个字都没有（2026-11-24 实测：
    # qwen3:4b 在 num_predict=1024 下思考写满 1024 token、正文 0 字）。本地服务自己就有兜底
    # （ollama 的 num_predict 默认 -1、llama.cpp 的 n_predict 默认 -1），不传就是想到自然结束。
    if not (_is_local and thinking_enabled is not False):
        payload["max_tokens"] = max_tokens
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
    # 显式关不是可有可无：思考 token 与正文**抢同一个输出上限**——OpenAI 的
    # `max_completion_tokens` 注释原文是 including visible output tokens and reasoning
    # tokens；Anthropic 的 thinking 文档写 thinking tokens count toward your max_tokens；
    # ollama 实测 completion_tokens 恒等于 max_tokens 满额。摘要这类短输出调用必须显式关，
    # 否则思考把预算吃满 → content 为空（2026-09-17 DeepSeek、2026-11-24 ollama 各见一次）。
    #
    # 三条通道说三种话，别互相借：
    #   DeepSeek → {"thinking": {"type": "enabled"|"disabled"}}
    #   本地     → {"reasoning_effort": "none"} 表示关；"开"不表态（思考型模型默认就思考，
    #              ollama 的档位对 Qwen3 这类只认开关的模型没有可测差别，2026-11-24 实测）
    #   其它公网 → 不表态，不替厂商做主（未知字段有的直接 400）
    _thinking_ok = provider_supports_thinking if provider_supports_thinking is not None else settings.is_deepseek_api
    if _is_local:
        # 本地端点认 reasoning_effort、不认 DeepSeek 的 thinking 对象；只在明确关闭时发。
        if thinking_enabled is False:
            payload["reasoning_effort"] = "none"
    elif _thinking_ok and thinking_enabled is not None:
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
    system_layout: str = ANYWHERE,
) -> dict:
    """
    非流式聊天补全 — 当前生产路径。
    """
    url, headers = _build_request_url_and_headers(api_base_url, api_key)
    def _payload() -> dict:
        return _build_chat_payload(
            messages=messages, model=model, api_base_url=api_base_url,
            temperature=temperature, top_p=top_p,
            max_tokens=max_tokens, tools=tools, response_format=response_format,
            presence_penalty=presence_penalty, frequency_penalty=frequency_penalty,
            thinking_enabled=thinking_enabled, user_id=user_id,
            provider_supports_thinking=provider_supports_thinking,
            system_layout=system_layout,
        )

    async with httpx.AsyncClient(timeout=_request_timeout(api_base_url)) as client:
        response = await client.post(url, json=_payload(), headers=headers)

        # 400 且错误指向「引用的文件没了」→ 把 file 块降级成 [图片] 占位，重发一次。
        # 只重试一次：第二次还报 file 错说明不是失效问题，再试就是死循环。
        if response.status_code == 400 and _looks_like_stale_file(response.text):
            replaced, stale = _degrade_file_blocks(messages)
            if replaced:
                if stale:
                    from app.services.ai.deepseek_files import invalidate
                    invalidate(stale)
                logger.warning(f"🖼️ 远端图片已失效，{replaced} 张降级为占位后重发一次")
                response = await client.post(url, json=_payload(), headers=headers)

        if response.status_code != 200:
            error_text = response.text[:500]
            logger.error(f"LLM API 错误 ({response.status_code}): {error_text}")
            _raise_classified_error(response.status_code, error_text,
                                    pool_key_id=pool_key_id,
                                    base_url=api_base_url)
            return {}  # unreachable, 但保持类型安全

        data = response.json()
        choice = data["choices"][0]
        message = choice["message"]

        # 归一到顶层键（reasoning_tokens / cached_tokens）——口径见 llm_usage
        usage = normalize_usage(data.get("usage"))

        result = {
            "content": message.get("content"),
            "tool_calls": message.get("tool_calls"),
            "usage": usage,
            "finish_reason": choice.get("finish_reason", "stop"),
        }
        # 思考内容有**两个名字**：DeepSeek 叫 reasoning_content，ollama 的 OpenAI
        # 兼容层叫 reasoning（llama.cpp 给的是 reasoning_content）。只读一个名字的后果
        # 是「思考了一整轮、正文空、这里也没有」——用户看到的现象是 AI 一言不发。
        reasoning = message.get("reasoning_content") or message.get("reasoning")
        if reasoning:
            result["reasoning_content"] = reasoning
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
    system_layout: str = ANYWHERE,
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
        messages=messages, model=model, api_base_url=api_base_url,
        temperature=temperature, top_p=top_p,
        max_tokens=max_tokens, tools=tools, response_format=response_format,
        presence_penalty=presence_penalty, frequency_penalty=frequency_penalty,
        thinking_enabled=thinking_enabled, user_id=user_id,
        provider_supports_thinking=provider_supports_thinking, stream=True,
        system_layout=system_layout,
    )

    full_content = ""
    full_reasoning = ""
    finish_reason = "stop"
    usage: dict = {}

    # 工具调用累加器（流式模式下 tool_calls 分多个 chunk 到达）
    tool_call_acc: dict[int, dict] = {}  # index → {id, name, arguments}
    dispatched: set[int] = set()  # 已通过 on_tool_call 分发的 index

    async with httpx.AsyncClient(timeout=_request_timeout(api_base_url)) as client:
        async with client.stream("POST", url, json=payload, headers=headers) as response:
            if response.status_code != 200:
                error_text = (await response.aread()).decode()[:500]
                logger.error(f"LLM API 错误 ({response.status_code}): {error_text}")
                _raise_classified_error(response.status_code, error_text,
                                    pool_key_id=pool_key_id,
                                    base_url=api_base_url)

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
                        usage = normalize_usage(chunk_usage)

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

                # 累加推理内容（仅日志记录，不推送前端）。
                # 两个字段名都要认：DeepSeek/llama.cpp 用 reasoning_content，ollama 用 reasoning。
                _reasoning_delta = delta.get("reasoning_content") or delta.get("reasoning")
                if _reasoning_delta:
                    full_reasoning += _reasoning_delta

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


def _raise_classified_error(status_code: int, error_text: str,
                            pool_key_id: int | None = None,
                            base_url: str = ""):
    """
    按状态码分类抛出对应的异常：
    - 400 + 模板报错 → SystemLayoutError（布局结论错了，学习后下一轮生效）
    - 429 → RateLimitError（换 Key 重试）
    - 500/503 → ServerError（同 Key 等待重试）
    - 402/401 → KeyFatalError（跳过此 Key）
    - 其他 → 普通 Exception
    """
    if status_code == 400 and is_system_layout_error(error_text):
        raise SystemLayoutError(base_url, error_text)
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

    尾巴只是「最后说到哪」的收窄版（轮数/字数是 runtime 配置）：那一场干过什么由经历行
    在切场的第一轮补进账本（见 _deliver_recollection），两段文案分开放，边界也分开写。

    放在历史之后、当前时间之前——动态内容沉底，前缀（system + 历史）仍可缓存。
    便签不走这里：它是**投递制**，投进来就固化在会话前缀里（见 _deliver_frame_notes）。
    """
    try:
        from app.config import settings
        from app.services.agent.state_stack_service import frame_turn_context
        from app.utils.pure.state_stack import frame_tail

        tail = frame_tail(messages,
                          max_exchanges=settings.handoff_tail_exchanges,
                          max_chars=settings.handoff_tail_chars)
        block = await frame_turn_context(db, agent.id, context_ref, tail)
        if block:
            messages.append({"role": "system", "content": block})
    except Exception as e:
        logger.warning(f"交接尾巴注入失败（非致命）: {e}")


async def _deliver_plan_board(db, agent, context_ref: str) -> None:
    """计划板：与记忆同一条管线，落在当轮新消息之前（见 docs/dev/plan_and_alarm.md §3.2）。"""
    from app.services.agent.plan_service import deliver_plans
    await deliver_plans(db, agent, context_ref)


async def _deliver_recollection(
    db, agent, context_ref: str, *, query: str, recalled_memory_ids: list[int],
) -> None:
    """记忆与经历同一条管线：抢在 sync 之前落账本（先想起，再读新话）。

    额度按**真正投出去**的条数算（不是召回到几条）：这一轮记忆全都投过了，经历就该多拿几条。
    切场那一轮先把「刚离开那一场」的行投满，剩额才给按语境召回——刚切过来时当前消息还没
    生效，按语境找不到什么，额度花在上一场更值。

    场景取当前帧（`current_scene`），不取会话键：插件帧里干的事落在插件帧的场景上，
    但「投过没有」仍记在会话账本里（`context_ref`）——同一段对话里的账本就是那一本。
    """
    from app.config import settings
    from app.services.agent.state_stack_service import current_scene
    from app.services.experience.experience_delivery import (
        deliver_recalled_experiences, deliver_switch_experiences,
    )
    from app.services.memory.memory_delivery import deliver_memories
    from app.utils.pure.injection_budget import experience_slots

    delivered = await deliver_memories(db, agent, context_ref, recalled_memory_ids)
    limit = experience_slots(
        len(delivered),
        memory_max=settings.memory_inject_max,
        experience_max=settings.experience_inject_max,
        total_max=settings.inject_max_total,
    )
    scene = await current_scene(db, agent.id)
    scene_ref = str(scene.get("context_ref") or "") or context_ref
    switched = await deliver_switch_experiences(
        db, owner_id=agent.id, context_ref=context_ref, scene=scene_ref,
        handoff=scene.get("handoff") or {}, limit=limit,
    )
    await deliver_recalled_experiences(
        db, owner_id=agent.id, context_ref=context_ref, scene=scene_ref,
        limit=max(0, limit - len(switched)), query=query,
    )


async def _bound_world_sources(db, agent, group_id: int | None) -> list[str]:
    """这个会话可用的世界能力源（`world-{id}`）：群绑定优先，随后 agent 直接绑定，去重。

    与拼【本群世界】那段同一套绑定口径——清单里列出了哪些世界，它们的技能变更就该通知到
    哪个状态；两处各查一遍必然漂移（第一版就是各自查的，通知那边还查漏了 agent 绑定）。
    """
    from app.services.world.world_service import find_worlds_by_entity

    sources: list[str] = []
    seen: set[int] = set()
    for entity_type, entity_id in (("group", group_id), ("agent", agent.user_id or 0)):
        if not entity_id:
            continue
        for w in await find_worlds_by_entity(db, entity_type, entity_id):
            if w.id in seen:
                continue
            seen.add(w.id)
            sources.append(f"world-{w.id}")
    return sources


async def _collect_injection_events(db, agent, context_ref: str, entries: list[dict],
                                     prompt_source: str,
                                     world_sources: list[str] = ()) -> list[dict]:
    """历史之后、尾部读数之前的一次性事件：便签投递/撤下 + 能力变更通知。

    群聊与私信两条路径曾经各抄一遍这几行；语义要求是「紧跟历史」，所以收成一处——
    下次加事件源时才不会只改了一条路。环境变更由调用方追加（它已在构建前缀时算过）。
    world_sources：这个世界会话用得上的世界技能源。它们的定义是**按版本快照锁定**的，改动
    要等解锁才进前缀，所以必须像平台工具一样发通知；而通知只有走这里才落账本、才活过一轮
    （以前直接在拼【本群世界】那段 append 一条 system 消息，这一轮说完就没了）。
    """
    from app.utils.pure.history import make_entry

    events: list[dict] = await _deliver_frame_notes(db, agent, context_ref, entries)
    cap_notice = await _build_capability_notice(
        db, agent, context_ref, prompt_source, world_sources)
    if cap_notice:
        events.append(make_entry("notice", cap_notice, flags={"drop_on_unlock": True}))
    events += await _deliver_handover(db, agent, entries)
    events += await _deliver_focus_notices(db, agent)
    events += await _deliver_friend_requests(db, agent, entries)
    return events


async def _deliver_friend_requests(db, agent, entries: list[dict]) -> list[dict]:
    """待处理好友申请：**投递 = 落一条 notice**（带申请 id），处理掉/撤回后补一条作废。

    为什么不是每轮在尾部念一遍：申请可能挂好几天，尾部块每轮重拼就是每轮都付一次钱；
    它是"有人来敲过门"这件事，不是一份读数。幂等与撤下都借账本自己的 ref（同 _deliver_handover），
    不另立游标表——多一份状态就多一处漂移。
    """
    if getattr(agent, "ai_type", None) not in ("resonance", "general", "semi_general"):
        return []
    try:
        from app.repositories.friend_repo import SQLAlchemyFriendRepository
        from app.services.social.friend_service import get_pending_friend_requests_for_ai
        from app.utils.pure.friend_request import (
            delivered_request_ids, delivery_entry, retired_entry,
        )

        reqs = await get_pending_friend_requests_for_ai(
            friend_repo=SQLAlchemyFriendRepository(db), agent_user_id=agent.user_id)
        delivered = delivered_request_ids(entries)
        live = {int(r["id"]) for r in reqs if r.get("id") is not None}
        out = [delivery_entry(r) for r in reqs
               if r.get("id") is not None and int(r["id"]) not in delivered]
        out += [retired_entry(i) for i in sorted(delivered - live)]
        return out
    except Exception as e:
        logger.warning(f"好友申请投递失败（非致命）: {e}")
        return []


async def _deliver_focus_notices(db, agent) -> list[dict]:
    """空焦段告知**落历史**：锚在空焦段上的记忆在哪儿都召不回，而 AI 自己看不见这件事。

    为什么不在状态摘要里每轮念：尾部动态块每轮重拼、永远吃不到缓存，而这是一条一次性事实。
    投递进度借会话帧的 delivered 记（与触发规则共用），解锁归零 → 下段上下文重新提醒。
    """
    try:
        from app.services.agent import focus_service
        from app.services.agent.state_stack_service import load_trigger_state, save_trigger_state
        from app.utils.pure.focus import empty_notice_key, format_empty_notice
        from app.utils.pure.history import make_entry

        state = await load_trigger_state(db, agent.id)
        pending = await focus_service.empty_pending(db, agent.id, state.get("delivered"))
        if not pending:
            return []
        delivered = dict(state.get("delivered") or {})
        for focus in pending:
            delivered[empty_notice_key(focus["id"])] = True
        state["delivered"] = delivered
        await save_trigger_state(db, agent.id, state)
        # 不带 ref：幂等依据是会话帧上的投递键（帧一换/一解锁就重新提醒），ref 在这里只是噪音
        return [make_entry("notice", format_empty_notice(pending),
                           flags={"drop_on_unlock": True})]
    except Exception as e:
        logger.warning(f"空焦段告知投递失败（非致命）: {e}")
        return []


async def _deliver_handover(db, agent, entries: list[dict]) -> list[dict]:
    """状态帧后事（待交接清单 + 平台代销告知）**落历史**：一次性条目，按 ref 幂等。

    为什么放这里：一次性事实只有这一个落历史的地方（与便签、能力变更通知同一出口）。
    为什么不是每轮念：尾部动态块每轮重拼、永远吃不到缓存；落历史写一次，之后每轮命中。
    """
    try:
        from app.services.agent.state_stack_service import (
            clear_pending_notices, handover_snapshot,
        )
        from app.utils.pure.handover import (
            delivered_refs, drop_ref, format_drop_notice, format_handover_notice, handover_ref,
        )
        from app.utils.pure.history import make_entry

        frames, notices = await handover_snapshot(db, agent.id)
        seen = delivered_refs(entries)
        out: list[dict] = []
        for frame in frames:
            ref = handover_ref(str(frame.get("id") or ""))
            if str(frame.get("id") or "") and ref not in seen:
                out.append(make_entry("notice", format_handover_notice(frame), ref=ref,
                                      flags={"drop_on_unlock": True}))
        for note in notices:
            ref = drop_ref(str(note.get("at") or ""))
            if ref not in seen:
                out.append(make_entry("notice", format_drop_notice(note), ref=ref,
                                      flags={"drop_on_unlock": True}))
        if notices:
            await clear_pending_notices(db, agent.id)
        return out
    except Exception as e:
        logger.warning(f"状态后事告知投递失败（非致命）: {e}")
        return []


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


async def _build_capability_notice(db, agent, context_ref: str, prompt_source: str,
                                   extra_sources: list[str] = ()) -> str:
    """能力变更通知：按**本状态**的基线算增量 changelog（与注入同事务）。无变化返回空串。

    三个源都是"每个状态的前缀里装同一份"的全局内容（作用域=全部），所以每个状态各收一次。
    人格源由调用方传入：它是"本体 / 某个用户的覆盖"两种之一，只有拼前缀的那一层知道这一轮
    用的是哪个——这里再猜一次，猜错就会出现"通知发对了、解锁点去刷另一个源"的分裂。
    extra_sources：调用方这一轮真正用得上的其他源（世界技能 `world-{id}`）。并进来算，是因为
"变更通知"只该有一个出口——单独再拼一条既没有账本、也没有 drop_on_unlock。
    """
    try:
        from app.ai.prefix import notice_sources
        from app.repositories.capability_repo import SQLAlchemyCapabilityRepository
        from app.services.capability_versioning import build_change_notice
        # 源清单只有一份（ai/prefix.notice_sources）：新增一段有版本的内容只改那里，
        # 通知自动跟上，不会出现"前缀变了却没人被告知"。
        notice = await build_change_notice(
            SQLAlchemyCapabilityRepository(db), agent,
            notice_sources(agent.id, prompt_source, extra_sources),
            state=context_ref, foci=getattr(agent, "foci", None))
        return notice or ""
    except Exception as e:
        logger.warning(f"能力变更通知失败（非致命）: {e}")
        return ""


async def _inject_personality_anchor(db, agent, system_prompt: str, language: str = "zh") -> str:
    """
    人格锚点注入 — 设计文档 6.2：只读、始终在最前面、随一致性系数缩放。

    锚点存在于 personality_anchors 表时注入，不存在则原样返回。
    注入发生在所有段落拼接之后、工作区/状态链追加之前，保证锚点位于最前。
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


async def _build_emoji_section(db) -> str:
    """表情包段：**探测**（登记版本）+ 产出文本，两件事同一份内容。

    表情包清单是"进前缀且会变"的内容——加了包/删了包，字节就会变。以前它既不在版本链里、
    也不在通知源里：2026-11-14 加"大肥鱼"24 张，前缀从第 0 字节失效、却一条通知都没有。
    现在它和记忆索引、人格一样走版本链（作用域全部），变更由【能力变更通知】告知。

    内容上常驻只给包名、用法与写法，具体表情名走 list_emoji_faces：全量清单是每轮的固定
    开销，包一多即上千 token，而多数轮次并不发表情。
    """
    from app.repositories.capability_repo import SQLAlchemyCapabilityRepository
    from app.services.capability_versioning import (
        emoji_packs_source, ensure_text_source_version, SCOPE_ALL,
    )
    from app.services.plugin import catalog

    packs = await catalog.enabled_emoji_packs(db)
    if not packs:
        return ""
    lines = [
        "## 可用表情包",
        "表情写成短码（如 :smile:），旧写法 [表情:名字] 也认——一条消息最多一两个：",
    ]
    for pack in packs:
        usage = f"：{pack['usage']}" if pack["usage"] else ""
        lines.append(f"- **{pack['name']}**（{len(pack['faces'])} 个）{usage}")
    lines.append("具体有哪些表情、名字怎么写，调用 list_emoji_faces 查，别凭印象编名字。")
    text = "\n".join(lines)
    try:
        await ensure_text_source_version(
            SQLAlchemyCapabilityRepository(db), emoji_packs_source(), text, "表情包", scope=SCOPE_ALL)
    except Exception as e:
        logger.warning(f"表情包版本登记失败（非致命，本轮照常渲染）: {e}")
    return text


async def _build_tools_segment(db, agent, is_dm: bool = False, emoji_note: str = "") -> str:
    """tools 段：技能目录 + 要点——告诉模型「有什么、怎么调、要注意什么」。

    目录给全（每个可用工具一行，含最小调用形态），要点按层级与场景排；两者都不含 schema
    （schema 在请求的 tools 数组里，那一份**每轮取最新**，所以新能力立即可调）。这一段本身
    锁在 system 前缀里，渲染完全确定以避免字节漂移——代价是对话中途新增的能力不会立刻出现在
    目录里，由【能力变更通知】补位说明。
    """
    from app.services.tool_registry import get_allowed_tools
    from app.services.skill.skill_engine import _is_delay_reply_allowed
    from app.tools.base import SKILL_SEGMENT_META, ToolRegistry
    from app.utils.pure.agent_state import state_label

    delay_allowed = await _is_delay_reply_allowed(db, agent)
    current_tools = get_allowed_tools(
        agent.state, thinking_enabled=agent.thinking_enabled,
        delay_reply_allowed=delay_allowed,
        tool_scope=getattr(agent, "tool_scope", "all") or "all",
    )
    current_tool_names = {t["function"]["name"] for t in current_tools}

    # 🎭 状态帧工具隔离：当前帧有白名单就再过滤一层。世界帧的白名单是**现算**的
    # （基础集 + 这个世界当前颁布的技能，见 world_frame_service），这里只认同一份闸门
    from app.services.world.world_frame_service import active_world, frame_tool_gate
    gate = await frame_tool_gate(db, agent.id)
    frame_note = ""
    world_id_now = None
    if gate is not None:
        current_tool_names &= gate
        world_id_now, _ = await active_world(db, agent.id)
        frame_note = ("（你在世界里：只显示这个世界给你的能力）"
                      if world_id_now is not None
                      else f"（当前状态帧限定工具：{', '.join(sorted(gate))}）")

    # 场景决定要点里哪些「场景纪律」该出现（群聊/私信/世界），不影响权限
    scene = "world" if world_id_now is not None else ("dm" if is_dm else "group_chat")

    from app.utils.pure.tool_catalog import (
        collect_notes, render_catalog, render_notes,
    )

    entries = ToolRegistry.catalog_entries()
    # 要点支持两种写法：(层级, 正文) 或 (层级, 正文, {场景…})——后者只在该场景渲染
    segment_notes = []
    for key, meta in SKILL_SEGMENT_META.items():
        for note in (meta.get("usage_notes") or ()):
            level, text = note[0], note[1]
            scenes = note[2] if len(note) > 2 else (meta.get("usage_scenes") or frozenset())
            segment_notes.append({"segment": key, "level": level,
                                  "notes": [text], "scenes": scenes})

    lines = [
        "## 技能目录",
        f"你的状态：{state_label(agent.state)}　可用工具：{len(current_tool_names)} 个{frame_note}",
        *render_catalog(entries, current_tool_names),
    ]

    note_lines, dropped = render_notes(collect_notes(entries, segment_notes, scene=scene))
    if note_lines:
        lines.append("")
        lines.append("## 要点")
        lines.extend(note_lines)
        if dropped:
            lines.append(f"（另有 {dropped} 条要点未列出，需要时调 list_available_skills）")

    # 表情包段由探测阶段产好传进来（版本登记与渲染同一份内容，不重复查库）
    if emoji_note:
        lines.append("")
        lines.append(emoji_note)

    lines.append("")
    lines.append(
        "这份目录是本次对话开始时的快照：对话中途新增或改动过的能力可能没反映在这里，"
        "以工具表和【能力变更通知】为准；目录里还写着、但已经被撤掉的能力，调用会返回未知工具。"
        "要看全部能力调 list_available_skills。"
    )
    return "\n".join(lines)


async def _build_current_context(
    db: AsyncSession, agent, group_id: int,
    group_name: str, is_dm: bool,
    is_federated: bool = False,
) -> str:
    """current_context 段：当前时间（v0.2.1 精简——会话标题已用统一格式）"""
    from app.utils.pure.timeutil import local_time_text
    context = f"## 当前时间\n{local_time_text(settings.display_timezone)}\n"
    # 你的状态是「每轮读数」，和当前时间同档：放尾部按未命中计价，换来的是每轮都准。
    # 它不进环境段（环境是会话级事实，锁在 message 0 里等解锁），因为状态变化频繁，
    # 冻在前缀里会让 AI 一直按旧状态行事。
    from app.utils.pure.agent_state import state_line
    context += f"\n## 你的状态\n{state_line(getattr(agent, 'state', None))}\n"
    # 这三条曾经在这里各写一份（怎么发、思考看不见、支持 Markdown）：怎么发与"思考看不见"
    # 是锁定前缀里「认知模型」同一件事（消息只认工具），这里只留时间；Markdown 是能力声明，
    # 已挪进 core_identity 的消息格式段——尾部每轮按未命中计价，永不变的话别放这儿。
    # Federation context
    if is_federated:
        context += (
            "- **联邦共享**：此群聊已启用联邦共享，你的消息将自动同步到其他 Copree 实例，"
            "其他实例的用户可能会看到并回应你的消息。\n"
        )
    return context


def set_round_budget(messages: list[dict], *, round_no: int, total: int,
                     free: bool = False) -> bool:
    """把本轮轮次读数**追加**在上下文末尾（只追加，绝不改写已有的任何一条）。

    为什么当初想改写「## 当前时间」那条 system：它本来就在尾部，改它不动前缀。
    但它只在第一轮真的在尾部——轮内每多一条工具结果，它就被甩到中间去；再原地改写，
    等于把"它之后的所有工具结果"全部作废，每轮重算一遍。轮数一多就是平方级：
    2026-11-06 线上那一轮 18 次调用，重算 252,355 token，命中率从常态 96% 掉到 77%，
    一次就吃掉 30 万额度里的 85%。

    前缀缓存只认字节前缀，所以这条读数只能往末尾长。累积代价是一轮几十 token，
    与它省下的相比可以忽略；历史里那些旧读数由常规裁剪/压缩带走。

    返回 True = 写进去了；没有 messages 就返回 False（读数缺席不影响主流程）。
    """
    from app.utils.pure.round_budget import round_budget_text

    if not isinstance(messages, list) or not messages:
        return False
    messages.append({"role": "system", "content": round_budget_text(
        round_no=round_no, total=total, free=free)})
    return True


async def _recall_memory_ids(
    db: AsyncSession, agent, group_id: int,
    query_text: str,
    api_base_url: str | None, api_key: str | None,
    trigger_user_id: int | None = None,
    context_ref: str = "",
) -> list[int]:
    """本轮该想起哪几条记忆（只要 id）。投不投、投什么由 memory_delivery 按账本比对决定。

    检索词是最近几条消息、每轮都变；但"要不要投给 AI"不看检索词，看账本里这条记忆
    是哪一版。所以这里只召回，不渲染——渲染是投递那一步的事。

    v0.1.3: trigger_user_id 用于通用/半通用 AI 的 per-user 记忆隔离。
    context_ref + 当前语义焦段：决定哪几条记忆"够得着"（焦段锚点，见 focus_and_memory_reach §九）。
    """
    if not query_text.strip():
        return []
    try:
        from app.config import settings
        from app.services.agent.state_stack_service import get_active_semantic_focus
        memories = await recall_relevant_memories(
            db, agent.id,
            query=query_text,
            api_base_url=api_base_url or "https://api.deepseek.com",
            api_key=api_key,
            top_k=settings.memory_inject_max,
            group_id=group_id,
            user_id=trigger_user_id,
            ai_type=agent.ai_type or "resonance",
            call_count=agent.llm_call_count or 0,
            context_ref=context_ref,
            semantic_focus=await get_active_semantic_focus(db, agent.id),
        )
        return [int(m["id"]) for m in memories if m.get("id")]
    except Exception as e:
        logger.warning(f"记忆召回失败（非致命）: {e}")
        return []


async def _build_skill_injection(db: AsyncSession, agent, group_id: int) -> str:
    """injected_skills 段里的 Skill 引擎注入（记忆已改走账本条目，不在这一段）。"""
    try:
        from app.services.skill.skill_engine import evaluate_inject_skills
        skill_prompts = await evaluate_inject_skills(db, agent, group_id)
        if skill_prompts:
            return "## 当前激活的思维技能\n" + "\n".join(f"- {p}" for p in skill_prompts)
    except Exception as e:
        logger.warning(f"Skill 注入失败（非致命）: {e}")
    return ""


async def _build_memory_index(db, agent, state: str | None = None) -> str:
    """记忆索引（目录树）：**走版本链冻结**，所以它能安全地待在锁定段里。

    为什么必须冻结：索引原本每轮现读 DB，AI 中途 store_memory 一次，下一轮索引文本就变 →
    整个前缀从第 0 字节起 miss。它与当轮消息无关（只看 agent.id），本来就该在锁定段。
    冻结后：改动只写新版本 + 尾部 changelog 告知，解锁（compact/清空）后才对齐最新。
    """
    try:
        from app.repositories.capability_repo import SQLAlchemyCapabilityRepository
        from app.services.capability_versioning import (
            ensure_text_source_version, get_effective_text, memory_index_source, SCOPE_ALL,
        )
        from app.services.memory.structured_memory_service import format_db_records_for_prompt

        from app.services.agent.knowledge_service import knowledge_index_text

        cur = await format_db_records_for_prompt(db, agent.id)
        # 知识库索引与记忆索引同段注入、同一条版本链冻结：标题/摘要变了才换版本
        knowledge = await knowledge_index_text(db, agent.id)
        cur = "\n\n".join(x for x in (cur, knowledge) if x)
        if not cur:
            return ""
        repo = SQLAlchemyCapabilityRepository(db)
        source = memory_index_source(agent.id)
        # 索引是"每个状态的前缀里装同一份"的全局内容 → 作用域全部，变更通知到每个状态
        await ensure_text_source_version(repo, source, cur, f"AI{agent.id}记忆索引", scope=SCOPE_ALL)
        return await get_effective_text(repo, agent, source, cur, state=state)
    except Exception as e:
        logger.warning(f"记忆索引注入失败（非致命）: {e}")
        return ""


async def _attach_attachments_to_message(
    db,
    messages: list[dict],
    index: int | None,
    orm_message,
    data_dir: str,
    *,
    agent_id: int | None = None,
    context_ref: str | None = None,
    api_base_url: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    max_len: int | None = None,
) -> int:
    """把 messages[index] 的附件升级成模型能读的内容（图片 parts / 文本正文块）。

    只让"本轮"的带图消息携带真实图片，历史消息保持纯文本。这不是为了护 prompt cache：
    历史那段的图早在进账本时就已经定成 [图片]，尾巴上的"真图 → 标记"变化本来就不在前缀里
    （见 docs/dev/conversation_history.md §7）。这里只负责省钱——历史每轮重带图要多花
    vision token。文本文件同理，只在最新一条消息里内联正文。

    图片有两条路（2026-11-25）：
    - DeepSeek 官方端点 + `deepseek-flash` → 上传一次拿 `file_id`，请求里用
      `{"type": "file"}` 引用（不占 48 MiB 请求体，单图可到 64 MiB）；
    - 其他通道 → 继续 base64 内联（它们没有 Files API，硬发只会 404）。
    上传失败一律回退 base64：宁可多占点请求体，也不能让这张图凭空消失。

    返回实际注入的图片数（文本内容不计）。

    之前这里靠"格式化后的 content 里包含 ORM 原文"来反查是哪条消息，
    内容雷同时会匹配错；现在由调用方直接给出下标与 ORM 对象。
    """
    if index is None or orm_message is None:
        return 0
    attachments = getattr(orm_message, "attachments", None)
    if not attachments:
        return 0

    injected = 0
    images = image_attachments(attachments)
    if images:
        # 字数预算：文字先占，剩下的给图，另加一点余量。
        # **不按张数限制**——图片折算是官方视觉 token × 1.5 字/token（一张 276~1536 字），
        # max_len 是这条消息喂给 AI 时的字数上限（群设置 max_msg_display_len）；没有它的
        # 路径（私信 / 世界）传 None，即不限制。
        budget: int | None = None
        if max_len and max_len > 0:
            current = messages[index].get("content")
            text_len = len(current) if isinstance(current, str) else 0
            budget = max(0, max_len - text_len) + IMAGE_CHAR_HEADROOM
        from app.utils.pure.image_tokens import provider_of

        chosen = select_images_for_budget(
            images, data_dir, budget, provider_of(api_base_url, model))

        remote_ids: list[str] = []
        if chosen and agent_id is not None and context_ref:
            from app.services.ai.deepseek_files import (
                ensure_uploaded, file_scope, release_foreign_images,
                request_image_hashes, supports_files_api,
            )

            if supports_files_api(api_base_url or "", model):
                try:
                    # 换 key 的对账是**请求级**的：把这个请求体原本要用的所有图，
                    # 一起从旧作用域减掉；本次没用到的不动（换回来还能复用）
                    await release_foreign_images(
                        db, agent_id=agent_id, context_ref=context_ref,
                        keep_scope=file_scope(api_base_url or "", api_key),
                        content_hashes=await request_image_hashes(chosen, data_dir, len(chosen)),
                    )
                    for att in chosen:
                        remote = await ensure_uploaded(
                            db, agent_id=agent_id, context_ref=context_ref,
                            base_url=api_base_url or "", api_key=api_key,
                            data_dir=data_dir, att=att,
                        )
                        if not remote:
                            # 一张失败就整条回退 base64：别让同一条消息一半 file 一半 image_url
                            remote_ids = []
                            break
                        remote_ids.append(remote)
                except Exception as e:
                    logger.warning(f"🖼️ Files API 解析失败，回退 base64: {e}")
                    remote_ids = []

        if remote_ids:
            parts = [{"type": "text", "text": messages[index].get("content") or IMAGE_PLACEHOLDER}]
            parts.extend({"type": "file", "file_id": fid} for fid in remote_ids)
            messages[index]["content"] = parts
            injected = len(remote_ids)
        else:
            content = build_content(messages[index].get("content"), attachments, data_dir,
                                    images=chosen)
            if isinstance(content, list):      # 一张图都没读出来时保持纯文本
                messages[index]["content"] = content
                injected = injected_image_count(content)

    block = text_attachment_blocks(attachments, data_dir)
    if block:
        content = messages[index].get("content")
        if isinstance(content, list):
            content.append({"type": "text", "text": block})
        else:
            messages[index]["content"] = f"{content}\n\n{block}" if content else block
    return injected


async def _turn_image_messages(db, slots: list, orm_model, fallback_idx, fallback_orm):
    """本轮用户消息里**所有**带图消息 → [(下标, ORM)]（按时间正序）；没有就空列表。

    为什么不是"只认最后一条"：先发图、再补一句"收到了吗"是群里最常见的连发。只认最后一条
    时，那句文字会成为"最新一条用户消息"，图退化成历史里的 [图片] 标记——2026-11-14 真机
    上 AI 就是回"还是只有[图片]，我这边没收到内容"。

    为什么不是"只给一张"（2026-11-25 改）：群友连发三张，AI 只收到一张却按三条 [图片] 标记
    说"都看到了"。张数不该拍脑袋定——图片折算成字数，由注入层按每条消息的字数上限决定
    给几张（见 _attach_attachments_to_message 的 max_len）。
    """
    from app.utils.multimodal import image_attachments

    found: dict[int, object] = {}
    if fallback_orm is not None and image_attachments(getattr(fallback_orm, "attachments", None)):
        found[fallback_idx] = fallback_orm
    for index, entry in slots or []:
        ref = entry.get("ref")
        if not ref:
            continue
        orm = await db.get(orm_model, int(ref))
        if orm is not None and image_attachments(getattr(orm, "attachments", None)):
            found[index] = orm
    return sorted(found.items())

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
    导致 LLM 跨群回复（"历史重播"死循环）。新的「状态链系统」已通过状态链摘要
    提供跨任务上下文感知，不再需要原文注入。
    保留函数签名以兼容调用方（调用方均有 if cross_msgs 保护）。
    """
    return []


async def _versioned_personality(
    db: AsyncSession, agent, override: str | None, owner: int | None, state: str,
) -> tuple[str, str]:
    """人格段文本版本化：返回（这一轮的版本源, 这一轮用哪份文本）。

    **本体与 per-user 覆盖走同一套规矩**：写新版本 → 尾部 changelog 告知 → 各自解锁点换新。
    区别只有源——本体是 `agent-prompt-{id}`，覆盖是那个用户自己的源（两份文本不同，
    共用一个源会一轮写一个新版本）。
    为什么覆盖也进链：以前它是"per-user 配置、直接生效"的特例，而发起对话那一层每次都把
    **解析好的本体人格**当覆盖传下来，于是整条链在线上一次都没跑过（实证：37 个 AI 里只有 3 个
    有过 `agent-prompt-*` 版本），人格一改就把它所有会话的前缀一起作废。
    """
    from app.repositories.capability_repo import SQLAlchemyCapabilityRepository
    from app.services.capability_versioning import (
        agent_prompt_label, agent_prompt_source, ensure_text_source_version,
        get_effective_text, SCOPE_ALL,
    )
    # 人格段是全局内容（每个状态的前缀里装的都是它）→ 作用域全部
    body = getattr(agent, "current_system_prompt", None) or ""
    text = override or body
    source = agent_prompt_source(agent.id, override, owner)
    cap = SQLAlchemyCapabilityRepository(db)
    await ensure_text_source_version(
        cap, source, text, agent_prompt_label(agent.id, override, owner), scope=SCOPE_ALL)
    return source, await get_effective_text(cap, agent, source, text, state=state)


def _core_identity(overrides: dict) -> str:
    """核心身份段 + 平台出处（群与私信两条链路共用一份）。

    平台出处拼在**管理员覆盖之后**：面板上的覆盖是"我的 AI 该守什么规矩"的自由，
    而"你从哪来（Copree，开源）"是平台事实，不该被整段覆盖顺手抹掉。
    它落在 message 0 的锁定前缀里，一次性成本、之后都吃缓存。
    """
    base = overrides.get("core_identity") or CORE_IDENTITY
    return f"{base.rstrip()}\n\n{PLATFORM_ORIGIN}"


async def _dm_session_list_block(
    db: AsyncSession, agent, *, current_session_id: str = ""
) -> str:
    """「你在别处还有这些私信会话」：只给存在性、未读数与最后时间，不给内容。

    为什么不给内容：私信正文属于另一个会话。要看就调 read_conversation（一次一条，
    AI 知道自己读了什么），不是每轮把所有私信糊进上下文。群聊这边也要这块——
    2026-11-03 线上：AI 在群里被问"你能看到私信吗"，答"看不到"——它手上根本没有这份清单，
    工具（view_unread / read_conversation / send_dm）都在，但没人告诉它私信存在。
    """
    from sqlalchemy import func, or_

    if not getattr(agent, "user_id", None):
        return ""
    from app.models.dm import DMMessage, DMSession

    rows = (await db.execute(
        select(DMSession)
        .where(or_(DMSession.user1_id == agent.user_id, DMSession.user2_id == agent.user_id))
        .order_by(DMSession.last_message_at.desc().nullslast())
        .limit(8)
    )).scalars().all()
    rows = [ds for ds in rows if ds.session_id != current_session_id]
    if not rows:
        return ""

    lines = [
        "## 📂 你的私信会话",
        "（这些是别的会话，消息不在这里：看内容用 read_conversation（target_user_id=对方 users.id），"
        "说话用 send_dm。）",
    ]
    for ds in rows:
        other_id = ds.user2_id if ds.user1_id == agent.user_id else ds.user1_id
        oname = await display_name(db, other_id)
        unread = (await db.execute(
            select(func.count())
            .select_from(DMMessage)
            .where(
                DMMessage.session_id == ds.session_id,
                DMMessage.sender_id == other_id,
                DMMessage.read_at.is_(None),
            )
        )).scalar() or 0
        last_t = ds.last_message_at.strftime("%m-%d %H:%M") if ds.last_message_at else "—"
        unread_str = f"，{unread} 条未读" if unread else ""
        lines.append(f"- 私信「{oname}」(会话 {ds.session_id})：最后消息 {last_t}{unread_str}")
    return "\n".join(lines)


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
    prompt_owner: int | None = None,
    context_config: ContextConfig | None = None,
    pending_delivery: list[dict] | None = None,
    model: str | None = None,
) -> list[dict]:
    """
    构建发送给 LLM 的消息列表（6 段系统提示词 + 历史消息）。

    pending_delivery：调用方给一个 list，函数把"这次渲染进请求、还没投递过的账本条目"
    追加进去（见 utils/pure/history.undelivered）。真正的标记由 executor 在 LLM 响应
    回来之后做——渲染了却没发出去不算看过。
    

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

    # 本会话的键：账本、状态帧、前缀里的版本化源（提示词/记忆索引/能力）全按它记账，只拼这一处
    from app.services.history.context_sync import context_ref as _context_ref
    session_ref = _context_ref(group_id=group_id)

    # 前缀文本版本化：personality 走版本链（本体 / per-user 覆盖各一个源）
    # 改了 → 写新版本 + 尾部 changelog 告知增量，不碰前缀；compact 后整体生效
    personality_source, eff_personality = await _versioned_personality(
        db, agent, system_prompt_override, prompt_owner, session_ref)

    segments = {}
    if "core_identity" in enabled_segments:
        segments["core_identity"] = _core_identity(overrides)
    if "personality" in enabled_segments:
        segments["personality"] = build_personality_segment(agent, language, eff_personality)
    if "protocol" in enabled_segments:
        segments["protocol"] = overrides.get(f"protocol_{profile}") or protocol
    if "tools" in enabled_segments:
        # 表情包段自带版本登记（探测），与渲染同一份内容：一个口子，不重复查库
        segments["tools"] = await _build_tools_segment(
            db, agent, is_dm, emoji_note=await _build_emoji_section(db))
    # injected_skills（记忆索引 + 技能注入）拆成两半：索引只看能力版本，走版本链冻结，
    # 所以能待在锁定段；技能注入的检索词是「最近 5 条消息」，属于当轮事实，归尾部读数。
    recalled_memory_ids: list[int] = []
    if "injected_skills" in enabled_segments and context_config_parser.should_inject_skills(context_config):
        segments["injected_skills"] = await _build_memory_index(db, agent, session_ref)
        # 记忆不走尾部读数：它是账本条目，要落在当轮新消息之前（见下方账本段）
        recalled_memory_ids = await _recall_memory_ids(
            db, agent, group_id, query_text, api_base_url, api_key, trigger_user_id,
            context_ref=session_ref)

    order = context_config_parser.parse_segment_order(context_config)
    system_prompt = assemble_system_prompt(segments, order)

    # 环境段固定在锁定段末尾：它是这个会话的常量（只在解锁点对齐 env_locked），所以能待在
    # 锁定段；环境变了只往尾部投一条通知，这里的字节不动。不走 segment_order——
    # 它是平台机制，不是管理员可编辑的提示词段。
    # 本轮的环境判定一并在这里做掉：一次读帧、一次问通道，渲染与通知共用这一次取值。
    from app.services.plugin.channel import served_instances as _served_instances
    from app.services.plugin.environment import environment_turn
    from app.utils.pure.plugin_env import render_channel_rules, render_environment
    _served = await _served_instances(db, group_id)   # 一轮取一次：通道规矩与环境共用
    _env_locked, _env_entry = await environment_turn(db, agent, group_id, served=_served)
    if _env_locked:
        system_prompt = f"{system_prompt}\n\n{render_environment(_env_locked)}"
        # 通道规矩是同一份快照里的第二条（平台自己那条）：它跟着环境一起锁定、一起只落通知，
        # 所以这里只是同一份取值的第二个渲染出口。随群播报模式变的那条 @ 规矩是运行期读数，沉在尾部
        _rules = render_channel_rules(_env_locked)
        if _rules:
            system_prompt = f"{system_prompt}\n\n{_rules}"

    # ✨ 人格锚点注入（只读，始终在最前面 — 设计文档 6.2）
    system_prompt = await _inject_personality_anchor(db, agent, system_prompt, language)

    # 锁定态前缀：拼装照旧跑（探测=各构建函数里的版本登记，只有一处真理），
    # 但**发出去的是上次钉下的字节**——目录/表情包/索引的代码怎么改都进不了活跃会话。
    # 每轮白算几次查询换来一条路径，这是刻意的取舍（见 ai/prefix.py 模块头）。
    system_prompt = prefix_locked(agent, session_ref, personality_source, system_prompt)

    # 动态内容一律沉到尾部（message 0 只留静态段）：状态链每次切会话都在变、任务/通道规矩/
    # 好友申请也会变——写进前缀等于每轮重建整个前缀，缓存全废。它们按「当轮事实」跟在历史后面。
    # 记忆是例外，但它走的是账本条目（追加在历史尾部），不是"提前到历史之前"
    tail_blocks: list[str] = []
    if "injected_skills" in enabled_segments and context_config_parser.should_inject_skills(context_config):
        skill_block = await _build_skill_injection(db, agent, group_id)
        if skill_block:
            tail_blocks.append(skill_block)

    # ✨ 工作区任务（配置驱动）
    if context_config_parser.should_inject_workspace(context_config):
        try:
            from app.services.agent.workspace_service import get_current_task_text
            task_text = await get_current_task_text(db, agent.id)
            if task_text:
                tail_blocks.append(task_text)
        except Exception as e:
            logger.warning(f"工作区上下文注入失败（非致命）: {e}")

    # ✨ 状态链摘要（配置驱动）
    if context_config_parser.should_inject_state_stack(context_config):
        try:
            from app.services.agent.state_stack_service import get_state_stack_summary
            stack_summary = await get_state_stack_summary(db, agent.id)
            if stack_summary:
                tail_blocks.append(stack_summary)
        except Exception as e:
            logger.warning(f"状态链摘要注入失败（非致命）: {e}")

    # 📡 通道规矩里唯一随运行期变的一条（@其他成员会不会断在中间）：其余在锁定段里
    if group_id:
        try:
            from app.services.plugin import channel as channel_service
            mention_rule = await channel_service.live_mention_rule(db, group_id, served=_served)
            if mention_rule:
                tail_blocks.append(mention_rule)
        except Exception as e:
            logger.warning(f"通道播报模式说明注入失败（非致命）: {e}")

    # message 0 之后直接进对话历史：这里曾经有个"当轮注入区"（dynamic_readings），
    # 每轮重算的块放在历史之前会把后面整段前缀缓存打掉——能力索引已冻结进 message 0，
    # 其余当轮事实（技能、任务、状态、时间）一律沉到尾部读数或账本条目
    # 发出去之前再核对一次：以后若有人在这行之后又往 system_prompt 里塞东西，这里当场报错，
    # 而不是悄悄让所有人的前缀从头失效（口径见 ai/prefix.py）
    prefix_guard(agent, session_ref, personality_source, system_prompt)
    messages = [{"role": "system", "content": system_prompt}]

    # ── 帧清单：一次给全「你手上有哪些帧」，省掉每轮先 list_states 找 id ──
    # 位置就是"稳定前缀之后、历史之前"：清单在解锁点之间字节不动，帧的增删由账本条目补，
    # 所以它不会每轮改写历史之前那块（那会废掉后面整段前缀缓存）
    from app.services.agent.state_stack_service import frame_roster

    roster = await frame_roster(db, agent)
    if roster:
        messages.append({"role": "system", "content": roster})

    # ── 多会话上下文（配置驱动）──
    if context_config_parser.should_inject_cross_conversation(context_config):
        cross_msgs = await _build_cross_conversation_context(
            db, agent, current_group_id=group_id, trigger_user_id=trigger_user_id,
        )
        if cross_msgs:
            messages.extend(cross_msgs)
            logger.info(f"  AI {agent.name}: 加入 {len(cross_msgs)} 条多会话上下文")

    # ── 当前群聊（最后一个会话标题，位置即语义）──
    # 「点名环境」就点在这一行：哪个会话 + 这个会话该用哪个工具。它是每轮同样的字节（缓存里），
    # 也是全请求唯一能说清"你现在在哪"的地方——message 0 是静态段，不可能知道这次是群还是私信。
    messages.append({"role": "system", "content": f"在群聊「{group_name}」(id={group_id})中：发言用 send_gm。"})

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
        from app.services.history.context_sync import append_events, sync_group_history
        from app.utils.pure.history import FOLD_LIMIT, ROLE_BY_ACTOR, latest_message_ref, make_entry, undelivered

        # 群设置优先；没设过就用折叠默认值。0 是"不折叠"，不能当假值兜掉
        max_len = getattr(group_obj, "max_msg_display_len", None) if group_obj else None
        if max_len is None:
            max_len = FOLD_LIMIT

        group_ref = session_ref

        # 记忆与经历条目**必须排在当轮新消息之前**：「先想起这个人，再读他说的话」。
        # 所以它抢在 sync 之前落账本——sync 一追加，新消息就压到它前面去了。
        await _deliver_recollection(
            db, agent, group_ref, query=query_text, recalled_memory_ids=recalled_memory_ids)
        # 计划板同一条管线：先投计划、再 sync 落当轮消息，板子就紧挨着新消息之前
        await _deliver_plan_board(db, agent, group_ref)

        # ── 历史消息：账本（只追加 + 缺口）──
        # 旧实现每轮按「最新 N 条」重建窗口，前缀每轮前移 → 整段 miss；
        # 现在渲染即落库（条目 content 就是发给模型的最终字节），新消息只往后追加。
        ledger = await sync_group_history(db, agent, group_id, cap=max_unread, max_len=max_len)

        # 一次性事件（能力变更通知 / 便签撤下）**落成条目**：它们属于「外界带来了什么」，
        # 必须紧跟历史——落在尾部读数之前，否则下一轮它们会从末尾跑到中间（顺序变 = 断缓存）。
        # 便签投递（逐条条目、幂等）+ 撤下通知：投过没有以账本为准
        events = await _collect_injection_events(
            db, agent, group_ref, ledger, personality_source,
            world_sources=await _bound_world_sources(db, agent, group_id))
        if _env_entry:
            events.append(_env_entry)
        ledger = ledger + await append_events(db, agent, group_ref, events)

        last_user_idx = None
        last_user_orm = None
        # 本轮的用户消息（按时间序）：图可能不在"最后一条"上（先发图、再跟一句文字），
        # 所以要能往回找本轮里最近的一张图
        turn_user_slots: list[tuple[int, dict]] = []
        for entry in ledger:
            messages.append({
                "role": ROLE_BY_ACTOR.get(entry["actor"], "system"),
                "content": entry["content"],
            })
            if entry["actor"] == "self":
                turn_user_slots = []          # 上一轮的图不算本轮
            elif entry["actor"] in ("user", "world"):
                turn_user_slots.append((len(messages) - 1, entry))
                last_user_idx = len(messages) - 1
                last_user_orm = (await db.get(MessageModel, int(entry["ref"]))) if entry.get("ref") else None

        if pending_delivery is not None:
            pending_delivery.extend(undelivered(ledger))

        if context_config_parser.should_inject_image(context_config):
            image_msgs = await _turn_image_messages(
                db, turn_user_slots, MessageModel, last_user_idx, last_user_orm,
            )
            _n_img = 0
            # 最近的带图消息先拿额度（同一条消息内保留第一张，由 select_images_for_budget 保证）
            for _idx, _orm in reversed(image_msgs):
                _n_img += await _attach_attachments_to_message(
                    db, messages, _idx, _orm, settings.data_dir,
                    agent_id=agent.id, context_ref=session_ref,
                    api_base_url=api_base_url, api_key=api_key, model=model,
                    # 群聊消息喂给 AI 的字数上限（群设置 max_msg_display_len）：
                    # 图片折算成字数后与正文一起受它约束，0 = 不折叠 = 图片不限制
                    max_len=max_len,
                )
            if _n_img:
                messages.append({"role": "system", "content": image_note(_n_img, source="本轮消息")})

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

    # 上一轮工具失败不再当轮 append：账本的 [本轮工具] 条目已记了同一笔（含失败原因，
    # 见 utils/pure/history.tool_ledger_note）。同一件事两处说，而且它在尾部——每轮按未命中计价。

    # 尾部动态块（顺序即语义：先「我该干什么」，再「这里的规矩」，与上一段对话的尾巴分开放）
    for block in tail_blocks:
        messages.append({"role": "system", "content": block})

    # 撤下的便签 / 能力变更通知都在上面落成账本条目了——**这里不再当轮 append**（说完就没了正是要修的）

    # 📎 上一段对话的原文尾巴（切会话时的临时交接，只出现一轮）
    await _inject_cross_state_context(db, agent, session_ref, messages)

    # 当前时间放在最后（每次变化，放末尾不影响前缀cache）
    current_ctx = await _build_current_context(db, agent, group_id, group_name, is_dm)
    messages.append({"role": "system", "content": current_ctx})

    # 绑定世界（群绑定 + agent 直接绑定）→ 能力清单 + 世界侧 skill 变更通知（动态尾部，缓存友好）
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
            # enter_world 那句必须说：不先进世界，他就不知道自己正在操作哪个世界
            wc_line = (
                "；另有 world_command 可发文本命令（直接交给世界程序，如 旅人移动到 2,3 / 我去 2,3 / 身份 签到）"
                if skill_names else
                "。可用 world_command 发送文本命令（直接交给世界程序，如 旅人移动到 2,3 / 我去 2,3 / 身份 签到）"
            ) + ("；想玩这个世界（包括在私信里）就调 enter_world——它就是本群的「进入群视界」技能："
                 "进去后你的工具会换成这个世界颁布的技能（外加切帧/退出这几种基础工具），"
                 "命令直达世界程序、不经过群聊；出来用 pop_state")
            messages.append({"role": "system", "content":
                f"【本群世界】本群绑定世界「{w.name}」（#{w.id}）。"
                + (f"你已获得世界颁布的技能工具，像调普通工具一样直接 function calling 调用：{'、'.join(skill_names)}" if skill_names else "")
                + dup_note
                + wc_line
                + "。命令会以你的名义出现在群里，可见可审计。"})
            # 世界技能的能力变更通知**不在这里发**：一次性事件必须落账本（见
            # _collect_injection_events）。直接 append 一条 system 消息只活到本轮结束，
            # 下一轮 AI 就按旧说法办事了——这正是账本机制要修的那个病。
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
    prompt_owner: int | None = None,
    pending_delivery: list[dict] | None = None,
    model: str | None = None,
) -> list[dict]:
    """构建 DM 私信的消息列表（6 段系统提示词 + DM 历史消息；pending_delivery 同 build_messages）"""
    from app.models.dm import DMMessage, DMSession
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
            partner_name = await display_name(db, partner_user_id)
    except Exception:
        pass

    from app.utils.pure.timeutil import local_time_text
    now_str = local_time_text(settings.display_timezone)

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
    # 前缀文本版本化（同 build_messages）：personality 走版本链
    # 本会话的键：与群路径同一个口径（私信的键就是 session_id），只拼这一处
    from app.services.history.context_sync import context_ref as _context_ref
    dm_ref = _context_ref(session_id=session_id)
    personality_source, eff_personality = await _versioned_personality(
        db, agent, system_prompt_override, prompt_owner, dm_ref)
    segments = {
        "core_identity": _core_identity(overrides),
        "personality": build_personality_segment(agent, language, eff_personality),
        "protocol": dm_protocol,
        "tools": await _build_tools_segment(
            db, agent, is_dm=True, emoji_note=await _build_emoji_section(db)),
        "injected_skills": await _build_memory_index(db, agent, dm_ref),  # 索引（冻结）进锁定段
    }
    # 记忆不走这里：它是账本条目，落在当轮新消息之前（见下方账本段）
    recalled_memory_ids = await _recall_memory_ids(
        db, agent, group_id=0,  # group_id=0 表示非群聊上下文
        query_text=query_text, api_base_url=api_base_url, api_key=api_key,
        trigger_user_id=trigger_user_id, context_ref=dm_ref,
    )

    order = await _get_segment_order(db)
    system_prompt = assemble_system_prompt(segments, order)

    # ✨ 人格锚点注入（只读，始终在最前面 — 设计文档 6.2）
    system_prompt = await _inject_personality_anchor(db, agent, system_prompt, language)

    # 锁定态前缀：与群聊同一把锁、同一个键口径（私信的键就是 session_id）
    system_prompt = prefix_locked(agent, dm_ref, personality_source, system_prompt)

    # 动态内容一律沉到尾部（message 0 只留静态段）：切会话时状态链会变，写进前缀
    # 等于每轮重建前缀、缓存全废。它们按「当轮事实」跟在历史后面。
    # 记忆是例外，但它走的是账本条目（追加在历史尾部），不是"提前到历史之前"
    tail_blocks: list[str] = []
    # 技能注入的检索词是「最近 5 条消息」，每轮都变 → 沉到尾部读数（§3）
    _skill_block = await _build_skill_injection(db, agent, group_id=0)
    if _skill_block:
        tail_blocks.append(_skill_block)

    # ✨ 工作区任务
    try:
        from app.services.agent.workspace_service import get_current_task_text
        task_text = await get_current_task_text(db, agent.id)
        if task_text:
            tail_blocks.append(task_text)
    except Exception as e:
        logger.warning(f"DM 工作区上下文注入失败（非致命）: {e}")

    # ✨ 状态链摘要（v0.2.1）
    try:
        from app.services.agent.state_stack_service import get_state_stack_summary
        stack_summary = await get_state_stack_summary(db, agent.id)
        if stack_summary:
            tail_blocks.append(stack_summary)
    except Exception as e:
        logger.warning(f"DM 状态链摘要注入失败（非致命）: {e}")

    # 📂 私信会话列表（v0.3.2）：AI 感知自己有哪些 DM 会话（存在性+未读数，不含内容）
    try:
        dm_block = await _dm_session_list_block(db, agent, current_session_id=session_id)
        if dm_block:
            tail_blocks.append(dm_block)
    except Exception as e:
        logger.warning(f"DM 会话列表注入失败（非致命）: {e}")

    # message 0 之后直接进对话历史：这里曾经有个"当轮注入区"（dynamic_readings），
    # 每轮重算的块放在历史之前会把后面整段前缀缓存打掉——能力索引已冻结进 message 0，
    # 其余当轮事实（技能、任务、状态、时间）一律沉到尾部读数或账本条目
    prefix_guard(agent, dm_ref, personality_source, system_prompt)  # 同上：发出去前核对
    messages = [{"role": "system", "content": system_prompt}]

    # ── 帧清单：与群聊同一块、同一位置（稳定前缀之后、历史之前）──
    from app.services.agent.state_stack_service import frame_roster as _frame_roster

    _roster = await _frame_roster(db, agent)
    if _roster:
        messages.append({"role": "system", "content": _roster})

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
    from app.services.history.context_sync import append_events, sync_dm_history
    from app.utils.pure.history import ROLE_BY_ACTOR, make_entry, undelivered


    # 记忆与经历抢在 sync 之前落账本：位置就是"当轮新消息之前"（同群聊）
    await _deliver_recollection(
        db, agent, dm_ref, query=query_text, recalled_memory_ids=recalled_memory_ids)
    # 计划板同一条管线：先投计划、再 sync 落当轮消息
    await _deliver_plan_board(db, agent, dm_ref)

    ledger = await sync_dm_history(db, agent, session_id, cap=limit)

    # 一次性事件（能力变更通知 / 便签撤下）落成条目：紧跟历史、排在尾部读数之前
    events = await _collect_injection_events(db, agent, dm_ref, ledger, personality_source)
    ledger = ledger + await append_events(db, agent, dm_ref, events)

    last_user_idx = None
    last_user_orm = None
    turn_user_slots: list[tuple[int, dict]] = []
    for entry in ledger:
        messages.append({
            "role": ROLE_BY_ACTOR.get(entry["actor"], "system"),
            "content": entry["content"],
        })
        if entry["actor"] == "self":
            turn_user_slots = []              # 上一轮的图不算本轮
        elif entry["actor"] in ("user", "world"):
            turn_user_slots.append((len(messages) - 1, entry))
            last_user_idx = len(messages) - 1
            last_user_orm = (await db.get(DMMessageModel, int(entry["ref"]))) if entry.get("ref") else None

    if pending_delivery is not None:
        pending_delivery.extend(undelivered(ledger))

    # 🖼️ 本轮里最近的一条带图消息注入真图（先图后文也认）
    image_msgs = await _turn_image_messages(
        db, turn_user_slots, DMMessageModel, last_user_idx, last_user_orm)
    _n_img = 0
    for _idx, _orm in reversed(image_msgs):
        _n_img += await _attach_attachments_to_message(
            db, messages, _idx, _orm, settings.data_dir,
            agent_id=agent.id, context_ref=_context_ref(session_id=session_id),
            api_base_url=api_base_url, api_key=api_key, model=model,
            # 私信没有群设置的字数上限 → 图片不限制（用户 2026-11-25 的口径）
            max_len=None,
        )
    if _n_img:
        messages.append({"role": "system", "content": image_note(_n_img, source="本轮消息")})

    # 上一轮工具失败不再当轮 append：账本的 [本轮工具] 条目已记了同一笔（含失败原因，
    # 见 utils/pure/history.tool_ledger_note）。同一件事两处说，而且它在尾部——每轮按未命中计价。

    # 能力变更通知已在上面的历史段落成账本条目了（b-2：DM 与群聊同一套）

    # 尾部动态块（顺序即语义：先「我该干什么」，再「我有哪些会话」）
    for block in tail_blocks:
        messages.append({"role": "system", "content": block})

    # 撤下的便签通知也在上面的历史段落成账本条目了

    # 📎 上一段对话的原文尾巴（切会话时的临时交接，只出现一轮）
    await _inject_cross_state_context(db, agent, dm_ref, messages)

    # 当前时间放在最后（每次变化，放末尾不影响前缀cache）
    dm_ctx = await _build_current_context(db, agent, 0, partner_name or "私信", is_dm=True)
    messages.append({"role": "system", "content": dm_ctx})
    return messages
