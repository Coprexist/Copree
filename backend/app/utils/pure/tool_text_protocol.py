"""不带 tools 的通道上的工具文本协议（纯函数，零 IO）。

**为什么需要这一层**：有些推理模型没有 function calling 能力——`tools` 参数会被对端
在调度前直接拒绝（ollama 对 deepseek-r1 返 400 `does not support tools`），chat 模板里
也没有 `tool` 角色，工具结果塞进去会被整条丢弃。但模型本身读得懂"工具说明书 + 调用标记"：
实测 r1 按约定吐出的 `<SEND_DM to="张三">你好</SEND_DM>` 一字不差。

所以这类通道改用文本协议：工具定义写进 system 前缀，模型用
`<tool_call>{"name":…,"arguments":…}</tool_call>` 表达调用，工具结果用
`<tool_response>…</tool_response>` 回到消息里——与 qwen3 这类原生模板的语义一一对应，
只是承载从 JSON 字段换成了正文。

**字节确定性是硬要求**：这段文本每轮进 system 前缀，工具顺序一旦依赖注册顺序，
一抖就是整段前缀失效（与 tool_catalog 同一纪律，ollama 实测 39.45s → 0.21s）。
所以工具按名字排序，JSON 序列化固定分隔符，不输出任何时间/计数这类每轮会变的东西。
"""
from __future__ import annotations

import json
import re

# 调用标记：非贪婪匹配到最近的一个 `</tool_call>`，一条回复里可以有多块
_TOOL_CALL_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)
_TAG_RE = re.compile(r"</?tool_call>|</?tool_response>", re.IGNORECASE)

# 协议正文是**常量**：不随工具清单变，缓存前缀因此永不失效。
# 工具清单不在这里重复——它在【技能目录】里，每工具一行含用途与参数形态，
# 那是全系统唯一的工具说明来源；这里只补它没有的那件事：调用怎么表达。
PROTOCOL_TEXT = """## 工具调用协议

你的通道没有原生函数调用能力，改用下面的文本协议表达调用。需要调用工具时，在回复里写：

<tool_call>{"name": "工具名", "arguments": {"参数名": 值}}</tool_call>

- 工具名和参数名必须从本次对话的【技能目录】里**原样照抄**：不能自己起名、翻译或换写法。
- 一条回复可以写多个 <tool_call> 块，按你希望执行的顺序排列。
- 数字和 true/false 不加引号；字符串用双引号，正文里的换行写成 \\n。
- 工具执行结果会以 <tool_response>…</tool_response> 回到对话里，看到结果再决定下一步。
- 不需要调用工具时正常说话即可，不要解释协议本身。"""


def render_tool_protocol(tools: list[dict] | None) -> str:
    """有可用工具时返回协议正文；一个都没有时返回空串（不发一段空协议）。"""
    for tool in tools or ():
        fn = tool.get("function") if isinstance(tool, dict) else None
        if isinstance(fn, dict) and str(fn.get("name") or "").strip():
            return PROTOCOL_TEXT
    return ""


def render_call_blocks(tool_calls: list[dict] | None) -> str:
    """把 OpenAI 形状的 tool_calls 渲染回文本标记（历史回放的对称面）。"""
    blocks = []
    for call in tool_calls or ():
        fn = call.get("function") if isinstance(call, dict) else None
        if not isinstance(fn, dict):
            continue
        name = str(fn.get("name") or "").strip()
        if not name:
            continue
        raw = fn.get("arguments")
        if isinstance(raw, str):
            try:
                args = json.loads(raw) if raw.strip() else {}
            except json.JSONDecodeError:
                args = {}
        else:
            args = raw if isinstance(raw, dict) else {}
        payload = json.dumps(
            {"name": name, "arguments": args},
            ensure_ascii=False, separators=(",", ":"),
        )
        blocks.append(f"<tool_call>{payload}</tool_call>")
    return "\n".join(blocks)


# 模型顺手包上的 markdown 代码块：7B 蒸馏模型更习惯这样表达"一个 JSON"
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def parse_tool_calls(text: str | None) -> tuple[list[dict], str]:
    """从回复正文里取出调用，返回 (OpenAI 形状的 tool_calls, 剥掉调用块后的正文)。

    三种承载体都认：约定的 <tool_call> 标记、模型顺手包上的 markdown 代码块、以及整段
    就是一个 JSON 的裸输出。实测 7B 蒸馏模型会写成后两种——它明白了"要发消息"，只是记
    不住标记形状；这里多认两种格式，比让它把系统提示原样复述回来有用。

    **块解析失败不猜、也不剥**：名字无效或参数不是对象就整块留在正文里，让这一轮按
    "只说了话"处理、由上层收进账本。替它编参数发出去的消息收不回来。
    """
    if not isinstance(text, str) or not text.strip():
        return [], text if isinstance(text, str) else ""

    blocks: list[tuple[str, tuple[int, int]]] = [
        (m.group(1), m.span()) for m in _TOOL_CALL_RE.finditer(text)
    ]
    if not blocks:
        blocks = [(m.group(1), m.span()) for m in _JSON_FENCE_RE.finditer(text)]
    if not blocks:
        stripped = text.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            start = text.index("{")
            blocks = [(stripped, (start, start + len(stripped)))]

    calls: list[dict] = []
    used: list[tuple[int, int]] = []
    for raw, span in blocks:
        call = _call_from_payload(raw, len(calls))
        if call is None:
            continue
        calls.append(call)
        used.append(span)

    rest = text
    for start, end in sorted(used, reverse=True):
        rest = rest[:start] + rest[end:]
    return calls, _TAG_RE.sub("", rest).strip()


def _call_from_payload(raw: str, index: int) -> dict | None:
    """一段 JSON → 一条 tool_call；形状不对返回 None。"""
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    name = payload.get("name") or payload.get("tool")
    if not isinstance(name, str) or not name.strip():
        return None
    raw_args = payload.get("arguments")
    if raw_args is None:
        args = {k: v for k, v in payload.items() if k not in ("name", "tool")}
    elif isinstance(raw_args, dict):
        args = raw_args
    elif isinstance(raw_args, str):
        try:
            parsed = json.loads(raw_args) if raw_args.strip() else {}
        except json.JSONDecodeError:
            parsed = None
        if not isinstance(parsed, dict):
            return None
        args = parsed
    else:
        return None
    return {
        "id": f"call_txt_{index}",
        "type": "function",
        "function": {"name": name.strip(), "arguments": json.dumps(args, ensure_ascii=False)},
    }


def project_for_text_tools(messages: list[dict], protocol: str) -> list[dict]:
    """把一份原生工具会话改写成纯文本会话：协议前置，工具结果与调用都落到正文。

    为什么必须在请求出口统一改写：`role:"tool"` 在 r1 的模板里没有任何分支承接，
    会被整条丢弃——模型看不到结果就会反复调用同一个工具。改写位置放在这里，
    executor 的工具执行链路一行不用动。
    """
    out: list[dict] = []
    if protocol:
        out.append({"role": "system", "content": protocol})
    for message in messages:
        if not isinstance(message, dict):
            out.append(message)
            continue
        role = message.get("role")
        if role == "tool":
            body = message.get("content")
            if not isinstance(body, str):
                try:
                    body = json.dumps(body, ensure_ascii=False)
                except (TypeError, ValueError):
                    body = str(body)
            out.append({"role": "user", "content": f"<tool_response>\n{body}\n</tool_response>"})
            continue
        calls = message.get("tool_calls")
        if role == "assistant" and calls:
            copy = dict(message)
            copy.pop("tool_calls", None)
            rendered = render_call_blocks(calls)
            base = copy.get("content")
            copy["content"] = f"{base}\n{rendered}" if isinstance(base, str) and base else rendered
            out.append(copy)
            continue
        out.append(message)
    return out
