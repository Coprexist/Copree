"""通道能力：从错误文本判定工具支持，并记住结论（纯函数 + 进程内短期记忆）。

**为什么需要这一层**：Copree 的 response_worker 每一轮都无条件把 `tools`（技能表）
塞进 payload——这是云端 API 的默认语义。但模型是否接受 function calling 是**模型自己
的属性**，不是通道的属性：

- OpenAI / DeepSeek：接受，正常。
- ollama 上的 deepseek-r1:7b：`400 {"error":"... does not support tools"}`，
  整轮回复直接失败，用户收不到任何消息。
- 之后的每一个小模型都可能是这两种之一，而且不会提前告诉你。

**处理原则与"模型不吃图片"那条降级阶梯完全同构**（见 multimodal 的
`is_vision_unsupported_error`）：判定宁可宽松——误判的代价是这一轮少了一组工具
（仍能正常回话），漏判的代价是用户彻底收不到回复。真实错误文本一律进日志。

**为什么要记**：一次 400 之后再重试一次就已经多花了一个来回。记住 `(base_url, model)`
之后，后续每轮直接不带 tools，省掉必然失败的第一次请求。

**为什么要有 TTL**：记忆只为了省一次往返，不该变成永久判决。用户升级 ollama、
换掉 `primary_model`、或者干脆在同一个 base_url 上切了模型，能力都会变。所以结论
只保留 `_NO_TOOLS_TTL_SECONDS`，过期后重新试探；进程重启则全部清空。
"""
from __future__ import annotations

import time

# 结论的保留时长（秒）。太短会把"必然失败的第一次请求"付得太勤；太长则模型能力
# 已经变了还按旧结论跑（明明能用工具却一直不带）。10 分钟是两者的折中。
_NO_TOOLS_TTL_SECONDS = 600.0

# (base_url, model) → 记为"不接受 tools"的 monotonic 时刻
_no_tools_memory: dict[tuple[str, str], float] = {}

# 错误文本里"说的是工具这件事"的线索
_TOOL_HINTS = (
    "tool", "function_call", "function calling", "function-calling",
    "工具", "函数调用",
)
# 错误文本里"对端拒绝了"的线索
_REJECT_HINTS = (
    "not support", "unsupported", "does not support", "doesn't support",
    "not allowed", "not available", "invalid", "unexpected", "unknown",
    "cannot", "can not", "不支持", "不允许", "无效", "未知",
)


def is_tools_unsupported_error(error_text: str) -> bool:
    """从 API 错误文本判断是不是"这个模型不接受 tools"。纯函数。

    判定宽松（同 `is_vision_unsupported_error`）：只要同时提到"工具"和"拒绝"，
    就按不支持处理。因为拒绝的写法各家不同（ollama 说 `does not support tools`，
    有的说 `unsupported parameter: tool_choice`），关键词比句式稳。
    """
    t = (error_text or "").lower()
    if not t:
        return False
    has_tools = any(k in t for k in _TOOL_HINTS)
    has_reject = any(k in t for k in _REJECT_HINTS)
    return has_tools and has_reject


def capability_key(api_base_url: str, model: str) -> tuple[str, str]:
    """把一次调用的两要素收敛成记忆键：base_url 去尾斜杠、两侧都小写。"""
    return (
        (api_base_url or "").strip().rstrip("/").lower(),
        (model or "").strip().lower(),
    )


def supports_tools(api_base_url: str, model: str) -> bool:
    """这个通道上这个模型现在还会不会接受 tools。未知能力一律按"接受"（宽松优先）。"""
    key = capability_key(api_base_url, model)
    stamp = _no_tools_memory.get(key)
    if stamp is None:
        return True
    if time.monotonic() - stamp > _NO_TOOLS_TTL_SECONDS:
        _no_tools_memory.pop(key, None)
        return True
    return False


def remember_no_tools(api_base_url: str, model: str) -> None:
    """记下"这个通道 + 这个模型不接受 tools"，后续请求直接不带。"""
    _no_tools_memory[capability_key(api_base_url, model)] = time.monotonic()


def forget_no_tools(api_base_url: str | None = None, model: str | None = None) -> None:
    """提前清掉结论（用户改了配置、或测试要干净的起点）。两者都不给则清空全部。"""
    if api_base_url is None and model is None:
        _no_tools_memory.clear()
        return
    _no_tools_memory.pop(capability_key(api_base_url or "", model or ""), None)
