"""按对端 chat 模板适配 system 消息位置（纯函数，零 IO）。

**为什么需要这一层**：Copree 为了前缀缓存，把"这一轮才知道"的信息全部作为 system
追加在对话末尾（见 llm.py 的 16 处 system append）。OpenAI / DeepSeek 容忍这种写法，
但模板是模型自带的，不同渲染器表现不同：

| 渲染器 | 中间 system 会怎样 |
|---|---|
| llama.cpp `--jinja` | 直接执行模型 Jinja；Qwen 的 `raise_exception` → 500 |
| ollama | 自己的 Go 模板，把多条 system 合并进 `.System`（挪到开头，内容不丢） |
| OpenAI / DeepSeek | 原样接受 |

**为什么要分「稳定块」和「动态块」**：缓存命中的是最长公共前缀。Copree 的布局天然是
「稳定前缀 + 历史 + 尾部读数」，所以：

- 历史**之前**的 system 是稳定块（人格、帧清单、会话定位，解锁点之间字节不动）
- 历史**之后**的 system 是动态块（记忆召回、上一轮交接、上下文读数、回合预算）

处理方式必须分开：稳定块合并进头部（一次性重算），动态块**保持原位降级为 user**
（断点位置不变）。若把动态块也挪到头部，每轮变的内容就跑到最前面，整段前缀每轮失效
——实测本地 ollama 上同样的 payload，缓存命中 0.21s、不命中 39.45s（180 倍）。

**判定宁可宽松**：认不出的形状一律 `anywhere`——保持今天已经能跑通的通道逐字节不变。
判定不碰网络；探测（拿到模板的那一端）在 services/agent/template_probe.py。
"""
from __future__ import annotations

import re

ANYWHERE = "anywhere"  # system 可以出现在任意位置（OpenAI / DeepSeek 语义）
LEADING = "leading"    # system 只能出现在开头，多条合并成一条
NONE = "none"          # 不接受 system 角色，并入第一条 user

LAYOUTS = (ANYWHERE, LEADING, NONE)

# 模板方言：同一个模型，不同渲染器拿到的模板是不同语言写的
JINJA = "jinja"  # llama.cpp --jinja：直接执行模型自带的 Jinja chat_template
GO = "go"        # ollama：server 自己的 Go 模板（{{ .System }} / {{ range .Messages }}）

# 动态块降级成 user 时的身份标记。不能靠 message["name"]（Qwen 的模板只读 content），
# 只能写进正文——与工具附图那句「不是用户发的」同一个思路。
INJECTION_MARK = "[系统] "

# 模板里"抛出异常"的两种写法：Jinja 的 raise_exception，以及 Python 式 raise
_RAISE_EXCEPTION_RE = re.compile(r"raise_exception\s*\(([^)\n]*)", re.IGNORECASE)
_RAISE_PLAIN_RE = re.compile(r"\braise\b([^\n]*)", re.IGNORECASE)
# system 参与比较的两种操作数顺序：== 'system' / 'system' ==
_SYSTEM_CMP_RE = re.compile(r"==\s*['\"]system['\"]|['\"]system['\"]\s*==", re.IGNORECASE)
# 守卫首条消息的写法：messages[0].role / messages[0]['role']，允许空格
_FIRST_MESSAGE_RE = re.compile(r"messages\s*\[\s*0\s*\]")

# raise 文本里的关键词 —— 只是"位置要求"和"根本不支持"两种意图
_POSITIONAL_HINTS = (
    "beginning", "at the start", "at the beginning", "must come first",
    "first message", "at position 0", "start of the conversation", "before any",
)
_NEGATIVE_HINTS = (
    "not supported", "unsupported", "not allowed", "not available",
    "cannot", "can't", "does not support", "doesn't support", "is not",
)

# 首条守卫的匹配窗口（字符数）。模板里 `{%- if messages[0].role == 'system' %}`
# 从 messages[0] 到 'system' 通常 10~20 个字符，48 足够宽又不会被别处的 system 混进来。
_GUARD_WINDOW = 48

# ollama 的 Go 模板：system 只在 `.System` 里输出一次，循环里按 Role 匹配
_GO_SYSTEM_REF = re.compile(r"\.System\b")
# 循环可能带变量：{{ range $i, $_ := .Messages }}
_GO_MESSAGE_LOOP = re.compile(r"range\b[^\n}]*?\.Messages")
_GO_SYSTEM_BRANCH = re.compile(r"eq\s+\.Role\s+[\"']system[\"']", re.IGNORECASE)
_GO_PROMPT_ONLY = re.compile(r"\{\{-?\s*\.Prompt\s*-?\}\}")


def normalize_layout(value: object) -> str:
    """把库里/前端来的脏值收敛成三档之一；非法值走 anywhere（宽松优先）。"""
    if isinstance(value, str):
        val = value.strip().lower()
        if val in LAYOUTS:
            return val
    return ANYWHERE


# 跑在本机的推理服务几乎都落在这些地址上（llama.cpp / Ollama / vLLM / LM Studio）。
# 只做字面判断：解析 DNS 或探端口都要 IO，而这个函数每轮对话都会调。
#
# host.docker.internal / gateway.docker.internal 是 Docker 固定提供的「本机」别名
# （Copree 跑在容器里、模型跑在宿主上是最常见的自托管形态）。它们是字面量，不是
# 任意内网主机名，所以不受下面那条「不认域名」的约定限制。
_PRIVATE_LITERAL_RE = re.compile(
    r"^(?:(?:localhost|0\.0\.0\.0"
    r"|host\.docker\.internal"
    r"|gateway\.docker\.internal)"
    r"|127\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|192\.168\.\d{1,3}\.\d{1,3}"
    r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
    r"|\[?::1\]?)$",
    re.IGNORECASE,
)


def guess_layout_from_host(base_url: str | None) -> str:
    """**没有任何判定证据时**按地址保守猜一档（纯函数，零 IO）。

    为什么需要它：用户配置档里的地址可能是自己填的（自建中转站、本机 llama-server），
    那条路径不一定跑过探测。此时库里的 anywhere 只是一个默认值，不是结论——
    照它拼出「中间 system」，llama.cpp 执行 Qwen 的 Jinja 就直接 500。

    - 字面私网 / 本机 → leading：system 合并到开头、动态块就地降为 user。
      三种本地服务的模板都接受这个形状，而「中间 system」它们未必容忍。
    - 其它（公网大厂 API）→ anywhere：保持今天的逐字节布局，前缀缓存不受影响。

    只认字面量，不解析域名：`http://gpu-box:8080` 这种内网主机名仍按公网处理，
    交给保存时的探测去拿真结论（猜错一边的代价是缓存，不是 500）。
    """
    if not isinstance(base_url, str) or not base_url.strip():
        return ANYWHERE
    authority = base_url.split("://", 1)[-1].split("/", 1)[0]
    # IPv6 的方括号里也有冒号：先整段取出 [::1]，再按冒号切端口
    if authority.startswith("["):
        host = authority.split("]", 1)[0] + "]"
    else:
        host = authority.split(":", 1)[0]
    return LEADING if _PRIVATE_LITERAL_RE.match(host) else ANYWHERE


def layout_from_template(template: str | None, dialect: str = JINJA) -> str:
    """从 chat 模板文本判定 system 该怎么摆。

    `dialect` 说明模板是谁的语法：llama.cpp 给的是模型自带 Jinja（`{%- if %}`），
    ollama 给的是它自己的 Go 模板（`{{ range .Messages }}`）。两种语言的判定规则不同，
    不能只看字符串长相猜。
    """
    if dialect == GO:
        return _layout_from_go_template(template)
    return _layout_from_jinja_template(template)


def _layout_from_jinja_template(template: str | None) -> str:
    """Jinja 模板（llama.cpp --jinja）。

    - 非字符串 / 空 → anywhere
    - raise 文本同时提到 system 与"位置" → leading
    - raise 文本同时提到 system 与"不支持" → none
    - 模板里根本没有 system 概念 → none
    - 所有 system 比较都被 `messages[0]` 守卫着（循环里没有通用分支）→ leading
    - 其余 → anywhere
    """
    if not isinstance(template, str) or not template.strip():
        return ANYWHERE

    positional = _raise_intent(template, _POSITIONAL_HINTS)
    negative = _raise_intent(template, _NEGATIVE_HINTS)
    if positional:
        return LEADING
    if negative:
        return NONE

    lowered = template.lower()
    if "system" not in lowered:
        # 模板连 system 这个角色都没有
        return NONE

    comparisons = list(_SYSTEM_CMP_RE.finditer(template))
    if not comparisons:
        return ANYWHERE

    for match in comparisons:
        window = template[max(0, match.start() - _GUARD_WINDOW):match.start()]
        if not _FIRST_MESSAGE_RE.search(window):
            # 有一处比较不在首条守卫里 —— 循环里能通用处理，任意位置都行
            return ANYWHERE
    return LEADING


def _layout_from_go_template(template: str | None) -> str:
    """Go 模板（ollama /api/show 的 template 字段）。

    ollama 的模板只有两个位置能放 system：开头的 `{{ .System }}`，以及
    `range .Messages` 循环体里对 `eq .Role "system"` 的分支。

    | 形状 | 判定 | 理由 |
    |---|---|---|
    | 有 `.System`、循环里没有 system 分支 | leading | ollama 只能吃一个 system，多的会被它自己合并到开头（缓存断点前移）；我们先合并，它就没有可合并的 |
    | 循环里也处理 system | anywhere | 位置能被正确保留，不动 |
    | 只有 `{{ .Prompt }}`（gemma4 这类） | anywhere | 没有角色概念，看不出限制；宁可宽松 |
    | 空 / 认不出 | anywhere | 同上 |
    """
    if not isinstance(template, str) or not template.strip():
        return ANYWHERE
    if _GO_PROMPT_ONLY.search(template) and not _GO_MESSAGE_LOOP.search(template):
        return ANYWHERE
    if not _GO_SYSTEM_REF.search(template):
        return ANYWHERE
    if not _GO_MESSAGE_LOOP.search(template):
        return ANYWHERE
    loop_start = _GO_MESSAGE_LOOP.search(template)
    body = template[loop_start.end():] if loop_start else template
    if _GO_SYSTEM_BRANCH.search(body):
        return ANYWHERE
    return LEADING


def _raise_intent(template: str, hints: tuple[str, ...]) -> bool:
    """模板是否抛出过「提到 system 且命中这些关键词」的异常。"""
    for pattern in (_RAISE_EXCEPTION_RE, _RAISE_PLAIN_RE):
        for match in pattern.finditer(template):
            text = match.group(1).lower()
            if "system" not in text:
                continue
            if any(hint in text for hint in hints):
                return True
    return False


def fit_system_layout(messages: list[dict], layout: str) -> list[dict]:
    """按 layout 调整 system 消息的位置，返回新 list。

    规则只有一条：**历史之前的 system 是稳定块，历史之后的是动态块**。

    - `anywhere`：原对象返回（零拷贝）。DeepSeek / OpenAI 的前缀缓存布局逐字节不变。
    - `leading`：稳定块按序合并成一条 system 放头部；动态块就地降级为 user。
    - `none`：稳定块合并后并入第一条 user；动态块就地降级为 user。

    动态块**不挪位置**——这是和"全部折叠到开头"的关键差别：挪了就把每轮变化的内容
    送到最前面，整段前缀每轮失效。就地只改 role，缓存断点位置与 anywhere 完全一致。

    已经符合 layout 的消息（如单条稳定 system 且无动态块）同样原对象返回。
    不修改入参：需要改的消息一律 copy 后再动。
    """
    layout = normalize_layout(layout)
    if layout == ANYWHERE or not messages:
        return messages

    if not any(_is_system(m) for m in messages):
        return messages

    # 历史起点 = 第一条非 system；之前的 system 是稳定块
    history_start = next(
        (i for i, m in enumerate(messages) if not _is_system(m)), len(messages)
    )
    stable = [m for m in messages[:history_start] if _is_system(m)]
    if not stable:
        # 没有稳定前缀可合并（开头就是对话）：不动，避免把动态块往前提
        return messages
    dynamic = [m for m in messages[history_start:] if _is_system(m)]

    body = [_as_injection(m) if _is_system(m) else m
            for m in messages[history_start:]]

    if layout == LEADING:
        if len(stable) == 1 and not dynamic:
            return messages  # 已经是规范形状，原对象返回
        return [{"role": "system", "content": _merge_system_contents(stable)}, *body]

    merged = _merge_system_contents(stable)
    return _fold_into_first_user(merged, body)


def _is_system(message: object) -> bool:
    return isinstance(message, dict) and message.get("role") == "system"


def _merge_system_contents(systems: list[dict]) -> str:
    """按原顺序合并 system 正文，`\n\n` 分隔；非字符串内容跳过。"""
    parts = [m.get("content") for m in systems]
    return "\n\n".join(p for p in parts if isinstance(p, str) and p)


def _as_injection(message: dict) -> dict:
    """一条动态 system 块 → 一条 user 消息，位置不变，正文前加身份标记。"""
    out = dict(message)
    out["role"] = "user"
    out["content"] = _prepend_text(INJECTION_MARK, out.get("content"))
    return out


def _fold_into_first_user(prefix: str, body: list[dict]) -> list[dict]:
    """把合并后的稳定块放到最前面，优先与第一条 user 共用一条消息。

    **第一条 user 前面还有消息时必须新建一条放在最前**，不能就地塞进它。历史窗口
    裁剪常从 assistant 的开头切（见本轮 69 条消息里 [4] 就是 assistant），就地塞会把
    稳定块——人格、状态、技能目录——丢到对话**中段**：位置随窗口每轮漂移（缓存前缀
    跟着断），模型读到的也是"我早就说过话了，怎么又来一段设定"，于是开始复述系统文字。
    稳定块的用意正是"整段前缀只重算一次"，那就必须真的在第一个字节。
    """
    index = next(
        (i for i, m in enumerate(body)
         if isinstance(m, dict) and m.get("role") == "user"),
        None,
    )
    if index is None:
        return [{"role": "user", "content": prefix}, *body]
    if index > 0:
        # 第一条 user 前面还有消息：新建一条放最前，**不动原有顺序**。把那条 user 提上来
        # 会颠倒"assistant 先说、user 后回"的时序，模型读到的就是错的对话顺序。
        return [{"role": "user", "content": prefix}, *body]
    head = dict(body[0])
    head["content"] = _prepend_text(prefix, head.get("content"), separator="\n\n")
    return [head, *body[1:]]


def _prepend_text(prefix: str, content: object, *, separator: str = "") -> object:
    """把一段文本放到 content 最前；content 可能是字符串，也可能是多模态的分片列表。

    `separator` 区分两种拼接：稳定块并入 user 是段落（`\n\n`），动态块的身份标记是
    紧贴正文（空串）—— 标记只是告诉模型"这行不是人说的"，不该另起一段。
    """
    if isinstance(content, list):
        return [{"type": "text", "text": prefix}, *content]
    body = content if isinstance(content, str) else ""
    return f"{prefix}{separator}{body}" if body else prefix.rstrip()
