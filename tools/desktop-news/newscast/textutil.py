"""文本与时间处理：清洗、指纹、相似度、时间解析。

新闻源给回来的标题/摘要里混着 HTML、实体、零宽字符，各源格式差异大；下游去重又要求
标题可比。所有规范化规则集中在这里，避免同一件事在多个模块各写一份。
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import html
import re
import unicodedata
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.I | re.S)
_WS_RE = re.compile(r"[ \t\u00a0\u3000]+")
_MULTI_NL_RE = re.compile(r"\n{2,}")
# 标题里常见的栏目/来源前缀，如「【视频】」「澎湃新闻：」，去掉后跨源比对才准
_TITLE_PREFIX_RE = re.compile(r"^\s*(?:【[^】]{0,12}】|\[[^\]]{0,12}\]|【[\u4e00-\u9fa5]{2,6}】)")
_TITLE_NOISE_RE = re.compile(r"[\s·・:：,，.。!！?？~～\-—_|｜/\\\"'“”‘’()（）\[\]【】<>《》]+")
_ZERO_WIDTH_RE = re.compile(r"[\u200b-\u200f\u2060\ufeff]")

# 中文习惯的时间文本，RSS 里偶尔出现（如「3小时前」）
_REL_TIME_RE = re.compile(r"(\d+)\s*(分钟|小时|天|周|个?月|年)前")


def strip_tags(raw: str) -> str:
    if not raw:
        return ""
    text = _SCRIPT_RE.sub(" ", str(raw))
    text = _TAG_RE.sub(" ", text)
    return html.unescape(text)


def clean_text(raw: str, limit: int | None = None) -> str:
    """去标签、去实体、去零宽字符、压缩空白。摘要字段统一走这里。"""
    if not raw:
        return ""
    text = str(raw)
    if "<" in text and ">" in text:
        text = strip_tags(text)
    else:
        text = html.unescape(text)
    text = _ZERO_WIDTH_RE.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _WS_RE.sub(" ", text)
    text = _MULTI_NL_RE.sub("\n", text).strip()
    text = text.strip(" \u3000")
    if limit is not None and len(text) > limit:
        text = text[: max(0, limit - 1)].rstrip() + "…"
    return text


def normalize_title(title: str) -> str:
    """标题归一化：用于跨源识别同一事件。只留实义字符，忽略标点与空白。"""
    text = clean_text(title)
    prev = None
    while prev != text:  # 前缀可能叠两层，如「【视频】【澎湃】」
        prev = text
        text = _TITLE_PREFIX_RE.sub("", text)
    text = unicodedata.normalize("NFKC", text)
    text = _TITLE_NOISE_RE.sub("", text)
    return text.lower()


def title_key(title: str) -> str:
    """相似度比较用的键：比 normalize_title 更宽松（去掉数字后仍能比对）。"""
    return normalize_title(title)


def fingerprint(title: str, url: str = "") -> str:
    """条目指纹：标题为主，标题缺失时退化为 URL。

    只按标题算指纹，是为了让「同一事件被多个源报道」在去重阶段能被识别出来；
    URL 里带的一堆跟踪参数反而会让同一条新闻算出不同指纹。
    """
    key = normalize_title(title)
    if not key:
        key = (url or "").strip()
    return hashlib.sha1(key.encode("utf-8", "ignore")).hexdigest()[:16]


def host_of(url: str) -> str:
    try:
        return urlsplit(url or "").netloc.lower()
    except ValueError:
        return ""


def _bigrams(text: str) -> set[str]:
    return {text[i:i + 2] for i in range(len(text) - 1)}


def similarity(a: str, b: str) -> float:
    """标题相似度 0~1，用于判断「两条新闻是不是同一件事」。

    单靠 SequenceMatcher 不够：同一事件换个说法（「试验二十八号卫星发射成功」vs
    「我国成功发射试验二十八号卫星 用于空间环境探测」）字符序列比只有 0.47，会漏合并。
    因此改成组合度量——序列比、二元组 Dice、包含度取最大，中文短标题上表现更稳。
    阈值由 classify 传入，默认 0.42 是按真实标题对校准出来的（应合并最低 0.43，
    不该合并最高 0.30，中间留了余量）。
    """
    ka, kb = title_key(a), title_key(b)
    if not ka or not kb:
        return 0.0
    if ka == kb:
        return 1.0
    if len(ka) <= 6 or len(kb) <= 6:
        # 极短标题（热搜词常见）没有足够的二元组，只认包含关系
        if ka in kb or kb in ka:
            return 0.92
        return SequenceMatcher(None, ka, kb).ratio() * 0.9

    seq = SequenceMatcher(None, ka, kb).ratio()
    bigrams_a, bigrams_b = _bigrams(ka), _bigrams(kb)
    if not bigrams_a or not bigrams_b:
        return seq
    inter = len(bigrams_a & bigrams_b)
    dice = 2 * inter / (len(bigrams_a) + len(bigrams_b))
    contain = inter / min(len(bigrams_a), len(bigrams_b))
    if inter < 2:
        # 只有一两个共同词组，多半是「苹果」这类同词不同事，压低分数
        return max(seq * 0.85, dice)
    return max(seq, dice, contain * 0.92)


def is_same_event(a: str, b: str, threshold: float = 0.42) -> bool:
    return similarity(a, b) >= threshold


def parse_time(value, default_tz_offset_hours: int = 8) -> _dt.datetime | None:
    """把各种时间格式解析成带时区的 datetime（失败返回 None）。

    支持：RFC822（RSS 标准）、ISO8601（Atom/JSON）、「YYYY-MM-DD HH:MM:SS」（国内站点常见，
    视为北京时间）、以及「N小时前」。无时区信息的一律按东八区处理——新闻源基本都是中文站。
    """
    if value is None:
        return None
    if isinstance(value, _dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=_dt.timezone(_dt.timedelta(hours=default_tz_offset_hours)))
    if isinstance(value, (int, float)):
        try:
            ts = float(value)
            if ts > 1e12:  # 毫秒时间戳
                ts /= 1000.0
            if ts <= 0:
                return None
            return _dt.datetime.fromtimestamp(ts, tz=_dt.timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None

    text = str(value).strip()
    if not text:
        return None

    rel = _REL_TIME_RE.search(text)
    if rel:
        num, unit = int(rel.group(1)), rel.group(2)
        delta = {
            "分钟": _dt.timedelta(minutes=num),
            "小时": _dt.timedelta(hours=num),
            "天": _dt.timedelta(days=num),
            "周": _dt.timedelta(weeks=num),
            "月": _dt.timedelta(days=30 * num),
            "个月": _dt.timedelta(days=30 * num),
            "年": _dt.timedelta(days=365 * num),
        }[unit]
        return _dt.datetime.now(tz=_dt.timezone.utc) - delta

    try:
        dt = parsedate_to_datetime(text)
        if dt is not None:
            return dt if dt.tzinfo else dt.replace(tzinfo=_dt.timezone.utc)
    except (TypeError, ValueError, IndexError):
        pass

    iso = text.replace("Z", "+00:00")
    try:
        dt = _dt.datetime.fromisoformat(iso)
        return dt if dt.tzinfo else dt.replace(tzinfo=_dt.timezone(_dt.timedelta(hours=default_tz_offset_hours)))
    except ValueError:
        pass

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M",
                "%Y-%m-%d", "%Y/%m/%d", "%Y年%m月%d日 %H:%M", "%Y年%m月%d日"):
        try:
            dt = _dt.datetime.strptime(text[:len(fmt) + 4], fmt)
            return dt.replace(tzinfo=_dt.timezone(_dt.timedelta(hours=default_tz_offset_hours)))
        except ValueError:
            continue
    return None


def now_utc() -> _dt.datetime:
    return _dt.datetime.now(tz=_dt.timezone.utc)


def iso(dt: _dt.datetime | None) -> str:
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return dt.astimezone(_dt.timezone.utc).isoformat(timespec="seconds")


def format_local(dt: _dt.datetime | None, fmt: str = "%m-%d %H:%M") -> str:
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return dt.astimezone().strftime(fmt)


def format_span(start: _dt.datetime | None, end: _dt.datetime | None) -> str:
    """给界面用的时间窗口文本，如「08-19 07:00 → 08-20 07:00」。"""
    if start is None or end is None:
        return ""
    return f"{format_local(start)} → {format_local(end)}"
