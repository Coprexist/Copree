"""结果的相关性闸门、去重与重排。

相关性是硬闸门而不是加权项：搜索引擎对查不到的冷门词不返回空集，而是塞一批无关结果
（实测本机 Bing 对 Copree AIsChat 返回外设驱动站、对长中文句返回留学中介页），
不匹配的必须当「没有」，否则模型会把无关页面当答案端给用户。

判定只用字面重合（拉丁词元 + 中文二元组），不引入模型调用。代价是中文泛词查询会有假阳性，
调用方给越具体的实体词，闸门越准。
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

from app.tools.file_operations.search.authority import authority, domain_of
from app.tools.file_operations.search.plan import entity_terms, latin_terms

# 中文泛词：它们几乎出现在任何页面上，拿来判定相关性等于没判
_GENERIC_BIGRAMS = frozenset({
    "发布", "平台", "发展", "类似", "最新", "消息", "相关", "什么", "哪些",
    "怎么", "如何", "介绍", "推荐", "关于", "以及", "一个", "我们", "他们",
})

_CJK = re.compile(r"[\u4e00-\u9fff]")
_REL_AGO = re.compile(r"(\d+)\s*(分钟|小时|天|周|个月|年)前")


def cjk_bigrams(text: str) -> set[str]:
    """相邻汉字二元组——中文没有词边界，二元组是够用的字面指纹。"""
    chars = _CJK.findall(str(text or ""))
    return {chars[i] + chars[i + 1] for i in range(len(chars) - 1)}


def latin_hit(term: str, hay: str) -> bool:
    """拉丁词元按整词命中——否则 chat 会被 WeChatAppEX 这种串误判成相关。"""
    pattern = r"(?<![0-9a-z])" + re.escape(str(term).lower()) + r"(?![0-9a-z])"
    return re.search(pattern, hay) is not None


def relevant(query: str, title: str = "", snippet: str = "",
             *, require_adjacent: bool = False) -> bool:
    """结果是否与查询字面相关。

    有实体词时要求**全部**命中：只命中一个词不算——AIs Chat 里的 AIs 会命中 AIS 船舶系统，
    AIsChat github 里的 github 会命中任意 GitHub 页面。纯中文查询退回非泛词二元组。

    require_adjacent：机械拆词派生的候选要求相邻命中。拆出来的 AIs Chat 既钓到船舶 AIS，
    也钓到「Two AIs Talking ... Chat」这种同名站，只有整串出现才算数。
    """
    hay = (str(title or "") + " " + str(snippet or "")).lower()
    if not hay.strip():
        return False
    terms = entity_terms(query)
    if terms:
        if not all(latin_hit(t, hay) for t in terms):
            return False
        if require_adjacent and len(terms) > 1:
            return " ".join(terms).lower() in hay or "".join(terms).lower() in hay
        return True
    return any(b in hay for b in cjk_bigrams(query) - _GENERIC_BIGRAMS)


# ═══════════════════════════════════════════════════
# 去重：URL / 标题 / 内容指纹
# ═══════════════════════════════════════════════════

def _norm_url(url: str) -> str:
    try:
        parts = urlsplit(str(url or ""))
    except ValueError:
        return str(url or "")
    host = (parts.hostname or "").lower()
    path = (parts.path or "/").rstrip("/") or "/"
    return urlunsplit((parts.scheme or "https", host, path, parts.query, ""))


def _norm_title(title: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(title or "").lower())


def _hash64(token: str) -> int:
    return int.from_bytes(hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest(), "big")


def simhash(text: str) -> int:
    """64 位内容指纹（拉丁词 + 中文二元组）——同一篇稿子被多家转载时用来判重。"""
    bits = [0] * 64
    for token in latin_terms(text) + sorted(cjk_bigrams(text)):
        h = _hash64(token.lower())
        for i in range(64):
            bits[i] += 1 if (h >> i) & 1 else -1
    out = 0
    for i, b in enumerate(bits):
        if b > 0:
            out |= 1 << i
    return out


def _hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def dedupe(items: list[dict], *, simhash_distance: int = 3) -> list[dict]:
    """按 URL、标题、内容指纹三层判重，保留先出现的那条。"""
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    signatures: list[int] = []
    out: list[dict] = []
    for item in items:
        url, title = _norm_url(item.get("url", "")), _norm_title(item.get("title", ""))
        if url and url in seen_urls:
            continue
        if title and title in seen_titles:
            continue
        # 指纹取正文优先：转载会在标题上挂「（转载）」这类尾巴，正文才是判重依据。
        # 摘要太短不足以判重（「Copree 平台」这种会把不同页面判成同一篇），退回标题。
        body = str(item.get("snippet") or "").strip()
        sig = simhash(body if len(body) >= 30 else str(item.get("title", "")) )
        if any(_hamming(sig, s) <= simhash_distance for s in signatures):
            continue
        if url:
            seen_urls.add(url)
        if title:
            seen_titles.add(title)
        signatures.append(sig)
        out.append(item)
    return out


# ═══════════════════════════════════════════════════
# 排序：实体命中 + 权威 + 时间
# ═══════════════════════════════════════════════════

def _recency(published_at) -> float:
    """新近度得分 0~1；取不到日期给中性 0.5，不让「没日期」吃亏。"""
    text = str(published_at or "").strip()
    if not text:
        return 0.5
    days = None
    m = _REL_AGO.search(text)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        days = n * {"分钟": 0, "小时": 0, "天": 1, "周": 7, "个月": 30, "年": 365}[unit] or n / 1440
    else:
        try:
            from email.utils import parsedate_to_datetime
            parsed = parsedate_to_datetime(text)      # RSS 的 pubDate 是 RFC 822
            if parsed is not None:
                dt = parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
                return _days_score((datetime.now(timezone.utc) - dt).total_seconds() / 86400)
        except (TypeError, ValueError):
            pass
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            days = (datetime.now(timezone.utc) - dt).total_seconds() / 86400
        except ValueError:
            return 0.5
    if days is None:
        return 0.5
    return _days_score(days)


def _days_score(days: float) -> float:
    if days <= 7:
        return 1.0
    if days <= 30:
        return 0.8
    if days <= 180:
        return 0.6
    return 0.4


def score(item: dict, entity_terms=()) -> float:
    """实体命中 0.55 + 域名权威 0.30 + 新近 0.15。实体占大头——先看是不是它，再看站可不可信。"""
    terms = [str(t).lower() for t in (entity_terms or []) if str(t).strip()]
    hay = (str(item.get("title", "")) + " " + str(item.get("snippet", ""))).lower()
    if terms:
        entity_score = sum(1 for t in terms if latin_hit(t, hay)) / len(terms)
    else:
        entity_score = 0.5
    domain = item.get("domain") or domain_of(item.get("url", ""))
    return 0.55 * entity_score + 0.30 * authority(domain, entity_terms) + 0.15 * _recency(item.get("published_at"))


def rank(items: list[dict], *, entity_terms=(), per_domain: int = 3, limit: int = 8) -> list[dict]:
    """去重 -> 打分排序 -> 每域名限量 -> 截断。每域名限量是为了不让一个站刷满整页。"""
    unique = dedupe(list(items))
    for item in unique:
        item.setdefault("domain", domain_of(item.get("url", "")))
        item["score"] = round(score(item, entity_terms), 3)
        # 给出事实性的来源权重，不下「这是不是官网」的结论：那要模型自己判断，
        # 平台只提供域名、标题与先验，替它下结论就把判断力拿走了
        item["authority"] = round(authority(item["domain"], entity_terms), 2)
    unique.sort(key=lambda x: x["score"], reverse=True)
    counts: dict[str, int] = {}
    out: list[dict] = []
    for item in unique:
        domain = item.get("domain") or ""
        if domain and counts.get(domain, 0) >= per_domain:
            continue
        if domain:
            counts[domain] = counts.get(domain, 0) + 1
        out.append(item)
        if len(out) >= limit:
            break
    return out
