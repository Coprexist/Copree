"""
粘贴文本 / 纯文本文件 —— 兜底解析器。

用户从任何平台复制一段对话，形状无非几种：

    用户：帮我列个预算          A: hi              [2026-11-26 13:22] 我：…
    AI：好，我列一下            B: hello           Human: …

这个解析器只做一件事：认出「这一行是谁说的」，把连续的行归到同一段。
它不猜内容含义，也不做任何模型调用——认不出角色就当上一段正文的续行。
"""
from __future__ import annotations

import re
from datetime import datetime

from app.services.chat_import.parsers import Parser, register
from app.services.chat_import.schema import Transcript, Turn

# 角色名前缀。「我/用户/你/A/Human」是问话侧，「AI/助手/B/Assistant/各平台名」是答话侧。
USER_NAMES = ("用户", "我", "你", "提问", "A", "Human", "User", "Q")
AI_NAMES = ("AI", "助手", "回答", "B", "Assistant", "Bot", "DeepSeek", "ChatGPT",
            "Claude", "Kimi", "豆包", "文心一言", "通义千问", "Gemini", "Copilot")

# 兼容中英冒号；角色名后面可以跟时间戳，例如「用户 [13:22]：」
_ROLE_LINE = re.compile(
    r"^\s*(?:[#>*\-\s]*)(" + "|".join(re.escape(n) for n in sorted(USER_NAMES + AI_NAMES, key=len, reverse=True))
    + r")\s*(?:[（(]?\s*([0-9]{1,4}[-/][0-9]{1,2}[-/][0-9]{1,2}[ T][0-9:]{4,5}(?::[0-9]{2})?)\s*[)）]?)?\s*[:：]\s*(.*)$"
)
# 单独的标记行，如「## 用户 · #123 2026-11-26T13:22:35」
_MARK_LINE = re.compile(r"^\s*#{1,6}\s*(用户|我|AI|助手)\b[^\n]*$")


def _role_of(name: str) -> str:
    return "user" if name in USER_NAMES else "assistant"


def _time_of(raw: str | None) -> datetime | None:
    if not raw:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S",
                "%Y/%m/%d %H:%M", "%m-%d %H:%M", "%H:%M"):
        try:
            dt = datetime.strptime(raw, fmt)
        except ValueError:
            continue
        if fmt in ("%m-%d %H:%M", "%H:%M"):
            return None      # 缺年份的时间没意义，宁可不填
        return dt
    return None


def detect(payload: str, filename: str | None = None) -> float:
    """有角色前缀 → 高置信；纯文本 → 给基础分，让它当兜底。"""
    if not payload.strip():
        return 0.0
    head = "\n".join(payload.splitlines()[:200])
    hits = sum(1 for line in head.splitlines() if _ROLE_LINE.match(line))
    if hits >= 2:
        return 0.75
    if hits == 1:
        return 0.4
    return 0.3


def parse(payload: str, filename: str | None = None) -> Transcript:
    turns: list[Turn] = []
    current: Turn | None = None
    buf: list[str] = []

    def flush():
        nonlocal current, buf
        if current is not None:
            text = "\n".join(buf).strip()
            if text or current.attachments:
                current.content = text
                turns.append(current)
        current, buf = None, []

    for line in payload.splitlines():
        m = _ROLE_LINE.match(line)
        if m:
            flush()
            current = Turn(role=_role_of(m.group(1)), at=_time_of(m.group(2)))
            buf = [m.group(3)]
            continue
        if _MARK_LINE.match(line):
            flush()
            continue
        if current is None:
            # 还没出现角色行：整段先攒着，等第一段角色出现时归给它之前是不对的，
            # 所以这里直接当作「用户」的开场（粘贴内容多半以用户提问开始）。
            current = Turn(role="user")
        buf.append(line)

    flush()

    if not turns:
        # 完全没有角色标记：整段作为用户的一条。不猜另一半，也不假装能拆。
        text = payload.strip()
        if text:
            turns = [Turn(role="user", content=text)]

    warnings = []
    if not any(t.role == "assistant" for t in turns):
        warnings.append("没认出 AI 的回复，全部按提问侧导入——如果是完整对话，请检查角色前缀（如「用户：/AI：」）")

    return Transcript(
        origin=_origin(filename),
        title=(filename or "").rsplit("/", 1)[-1].rsplit(".", 1)[0] or None,
        participants={"user": "我", "assistant": "AI"},
        messages=turns,
        warnings=warnings,
    )


def _origin(filename: str | None):
    from app.services.chat_import.schema import Origin
    return Origin(platform="pasted", parser="pasted", parser_version=1,
                  source_url=None, fetched_at=None)


register(Parser(name="pasted", platform="粘贴文本", version=1,
                parse=parse, detect=detect))
