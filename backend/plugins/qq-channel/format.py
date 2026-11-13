"""通道里的纯函数：文本归一、渲染格式、消息切分。不进网络、不碰数据库。

放这里的东西只依赖标准库与入参，便于单独测试，也免得 plugin.py 越养越厚。
"""
from __future__ import annotations

import base64
import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# 互动召回（官方：只有单聊有 is_wakeup 字段；对方主动对话后 30 天内 4 个周期各 1 条）
RECALL_PERIOD_DAYS = (1, 3, 7, 30)
MIRROR_MIN_CHARS = 2               # 太短的正文（"嗯"、"?"）撞车概率太高，不认

# 外部锚点的「名字#N」后缀：同一个人在不同机器人下会被改成 #2/#3，认亲时按基准名比较
# 外部锚点的「名字#N」后缀：同一个人在不同机器人下会被改成 #2/#3，认亲时按基准名比较
_NAME_SUFFIX_RE = re.compile(r"#\d+$")


def _name_base(name: str) -> str:
    return _NAME_SUFFIX_RE.sub("", str(name or "").strip())

# 单条正文先整条发：官方只给了 40054007「消息长度超限」这个错误码、没给数字，
# 所以不猜上限——被平台拒了就在"认过的长度"和"刚被拒的长度"之间二分（见 _deliver）。
LENGTH_ERROR_HINTS = ("40054007", "长度超限")
# 同一个 (msg_id, msg_seq) 发第二次会被判"消息被去重"：说明这条没发出去，换号重发是安全的
DEDUP_ERROR_HINTS = ("40054005", "消息被去重")
TEXT_CHUNK_MIN = 64                # 二分的兜底下限：只保证不再原地打转，不代表平台认这么短

# 腾讯把表情塞进 content 发成一个标记：<faceType=1,faceId="6",ext="eyJ0ZXh0Ijoi5a6z576eIn0=">。
# ext 是 base64 的 JSON，带该表情的文本。原样入库时界面是一串标记、AI 也读不出情绪，故入站先
# 还原成 [表情:名字]，再由入口归一（catalog.normalize_emoji_text）换成对应的 unicode 字符。
_FACE_MARK_RE = re.compile(r'<faceType=\d+,faceId="[^"]*"(?:,ext="([^"]*)")?>')


def humanize_faces(text: str) -> str:
    """QQ 的表情标记 → 可读文本；ext 解不出来时留一个「[表情]」，别把半截标记留在正文里"""
    if not text or "<faceType=" not in text:
        return text

    def _one(match: re.Match) -> str:
        raw = match.group(1) or ""
        name = ""
        if raw:
            try:
                decoded = base64.b64decode(raw + "=" * (-len(raw) % 4))
                name = str(json.loads(decoded).get("text") or "").strip()
            except Exception:
                name = ""          # 认不出的 ext 不值得让整条消息失败
        return f"[表情:{name}]" if name else "[表情]"

    return _FACE_MARK_RE.sub(_one, text)

def _parse_group_map(raw: Any) -> dict[str, int]:
    """解析 group_map（QQ 群 openid → Copree 群 id）：规则在 app.utils.pure.channel_landing 一处，
    这里只负责把坏项记成日志（平台侧判断"这个群接没接通道"要用同一份规则）。"""
    from app.utils.pure.channel_landing import parse_group_map

    data, problems = parse_group_map(raw)
    for problem in problems:
        logger.warning(f"group_map {problem}")
    return data

def format_channel_origin(kind: str, instance: str, conversation: str) -> str:
    """消息来自哪个通道会话：`<通道>:<实例>:<通道侧会话>`（QQ 的会话 = 群/用户 openid）

    为什么记在消息行上：同一个 Copree 群可能被多个通道实例接着，出站要认得出
    "这条是哪个实例的通道消息"——只活在本进程内存里的索引，别的实例看不见。
    """
    return f"{kind}:{instance}:{conversation}"


def parse_channel_origin(kind: str, raw: Any) -> tuple[str, str] | None:
    """上面的反向解析 → (实例, 会话)；不是本通道的写法就返回 None（别的通道的消息不归我们管）"""
    parts = str(raw or "").split(":", 2)
    if len(parts) != 3 or parts[0] != kind:
        return None
    return parts[1], parts[2]

_MENTION_TOKEN_RE = re.compile(r"<@!?\d+>")

def _same_channel_text(left: Any, right: Any) -> bool:
    """两台机器人渲染出来的字节不一样，但"人说的那句话"是一样的——认亲比的是后者。

    差在哪：被 @ 的那台会在正文前补自己的唤醒令牌（<@!自己的id>），没被 @ 的那台不补；
    正文里被 @ 的成员也各自解析成自己那条通道下的令牌。所以先把令牌摘掉、空白归一，再逐字比。
    太短的正文（"嗯"、"?"）不认：摘完令牌就没什么好比的了，撞车概率还高（见 MIRROR_MIN_CHARS）。
    取舍：同一个人 3 秒内发的两条只差 @ 对象的话，会被认成同一句——比"带 @ 的消息条条落两遍"
    划算（摘掉令牌后 AI 看到的话本来就一样），所以这么定。
    """
    def stripped(text: Any) -> str:
        return " ".join(_MENTION_TOKEN_RE.sub(" ", str(text or "")).split())

    cleaned = stripped(left)
    return len(cleaned) >= MIRROR_MIN_CHARS and cleaned == stripped(right)

def _is_length_error(exc: Exception) -> bool:
    """平台的"这条太长"就认这一种错：按错误码/文案认，不靠 HTTP 状态猜"""
    text = str(exc)
    return any(hint in text for hint in LENGTH_ERROR_HINTS)


def _is_dedup_error(exc: Exception) -> bool:
    """平台的"这个序号用过了"：按错误码/文案认（40054005）"""
    text = str(exc)
    return any(hint in text for hint in DEDUP_ERROR_HINTS)

def split_message(text: str, limit: int) -> list[str]:
    """长文拆成几条发（官方单条有长度上限，超了整条被拒，不是截断）。

    优先在空行处断，其次单行，都没有才按字符硬切：按字符硬切会把 Markdown 语法切坏、
    也会把一句话劈成两半。断点太靠前（不足 limit 的三分之一）就不值得，宁可硬切。
    """
    text = str(text or "")
    if len(text) <= limit:
        return [text] if text else []
    pieces: list[str] = []
    rest = text
    while len(rest) > limit:
        window = rest[:limit]
        cut = window.rfind("\n\n")
        if cut < limit // 3:
            cut = window.rfind("\n")
        if cut < limit // 3:
            cut = limit
        pieces.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip("\n")
    if rest.strip():
        pieces.append(rest)
    return [p for p in pieces if p]

def recall_period(elapsed_days: float) -> int | None:
    """距对方最近一次主动对话过了多久 → 命中第几个召回周期；超出 30 天返回 None。

    官方四个周期是「当天 / 1-3 天 / 3-7 天 / 7-30 天」，边界取上界，
    所以 [0,1) [1,3) [3,7) [7,30) 各一个名额。
    """
    for index, upper in enumerate(RECALL_PERIOD_DAYS):
        if elapsed_days < upper:
            return index
    return None
