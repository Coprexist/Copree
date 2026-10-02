"""结果的相关性闸门、去重、多引擎融合与重排。

相关性是硬闸门而不是加权项：搜索引擎对查不到的冷门词不返回空集，而是塞一批无关结果
（实测本机 Bing 对 Copree AIsChat 返回外设驱动站、对长中文句返回留学中介页），
不匹配的必须当「没有」，否则模型会把无关页面当答案端给用户。

判定只用字面重合（拉丁词元 + 中文词块），不引入模型调用。中文侧的判据挂在**非泛词汉字块**上：
按单个二元组判会放水（「数字生命」十条全过闸，顶上却是阿拉伯数字书写规范），
按整串判又会把半句话式的检索式全毙掉——所以是「整块命中，长块允许一半以上二元组」。
中英混排时拉丁词**只加分不否决**：模型顺手音译的外文名（CyberLife）不该把真正的中文结果毙掉。
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

from app.tools.file_operations.search.authority import authority, domain_of
from app.tools.file_operations.search.plan import entity_terms, latin_terms, quoted_phrases

# 中文泛词：它们几乎出现在任何页面上，拿来判定相关性等于没判。
# 补词的口径 = 实测踩过的假阳性来源（「数字生命」钓到「阿拉伯数字书写规范」、
# 「赛博生命」钓到「赛博加速器」，都是命中一个泛词二元组就放行）
_GENERIC_BIGRAMS = frozenset({
    "发布", "平台", "发展", "类似", "最新", "消息", "相关", "什么", "哪些",
    "怎么", "如何", "介绍", "推荐", "关于", "以及", "一个", "我们", "他们",
    "数字", "项目", "开源", "社交", "群聊", "社区", "智能", "系统", "产品",
    "工具", "服务", "功能", "内容", "信息", "技术", "开发", "设计", "官方",
    "网站", "下载", "软件", "应用",
})

_CJK = re.compile(r"[\u4e00-\u9fff]")
_REL_AGO = re.compile(r"(\d+)\s*(分钟|小时|天|周|个月|年)前")


def cjk_bigrams(text: str) -> set[str]:
    """相邻汉字二元组——中文没有词边界，二元组是够用的字面指纹。"""
    chars = _CJK.findall(str(text or ""))
    return {chars[i] + chars[i + 1] for i in range(len(chars) - 1)}


_CJK_RUN = re.compile(r"[\u4e00-\u9fff]{2,}")


def cjk_blocks(text: str) -> list[str]:
    """查询里最长的连续汉字串（>=2 字）——中文侧的相关性判据挂在词块上，不挂在单个二元组上。"""
    return _CJK_RUN.findall(str(text or ""))


def is_generic_block(block: str) -> bool:
    """整块都是泛词二元组时，这块不拿来判相关性（「项目」「开源」「群聊」）。"""
    grams = {block[i:i + 2] for i in range(len(block) - 1)} or {block}
    return grams <= _GENERIC_BIGRAMS


def content_blocks(query: str) -> list[str]:
    """查询里判得动的汉字块：去掉纯泛词块之后剩下的那些。"""
    return [b for b in cjk_blocks(query) if not is_generic_block(b)]


def has_gate_signal(query: str) -> bool:
    """这条检索式有没有「判得动」的东西：实体词，或一个非泛词汉字块。

    没有 = 全是泛词（「项目 开源 平台」）；这种情况编排层要在 hint 里说清楚
    「是你这条检索式没有实体词」，不能让模型以为网上没有。
    """
    return bool(entity_terms(query)) or bool(content_blocks(query))


def _block_hit(block: str, hay: str, *, whole: bool = False) -> bool:
    """汉字块命中：整串出现最理想；长块允许部分命中（调用方常把半句话当检索式），
    但要求至少一半二元组、且不少于两个——「数字生命」只命中「数字」不算（实测过的假阳性）。"""
    if block in hay:
        return True
    if whole:
        return False
    grams = {block[i:i + 2] for i in range(len(block) - 1)}
    if len(grams) < 3:
        return False
    hits = sum(1 for g in grams if g in hay)
    return hits >= 2 and hits * 2 >= len(grams)


def latin_hit(term: str, hay: str) -> bool:
    """拉丁词元按整词命中——否则 chat 会被 WeChatAppEX 这种串误判成相关。"""
    pattern = r"(?<![0-9a-z])" + re.escape(str(term).lower()) + r"(?![0-9a-z])"
    return re.search(pattern, hay) is not None


def relevant(query: str, title: str = "", snippet: str = "",
             *, require_adjacent: bool = False) -> bool:
    """结果是否与查询字面相关。判定分语言，理由见文件头与 docs/dev/web_search.md。

    - 查询里有中文：看**非泛词汉字块**，整块命中或长块过半即算；一块都判不动就是全否。
      这时拉丁词只加分不否决（加分算在 score 里），否则模型猜的外文名会把中文结果全毙掉。
    - 纯拉丁查询：要求实体词**全部**命中——AIs Chat 里的 AIs 会命中 AIS 船舶系统，
      AIsChat github 里的 github 会命中任意 GitHub 页面。
    - 引号里的中文短语整串必须出现：调用方写引号就是要精确匹配。

    require_adjacent：机械拆词派生的候选要更严——中文块要求整串，拉丁词要求相邻出现
    （拆出来的 AIs Chat 既钓到船舶 AIS，也钓到「Two AIs Talking ... Chat」这种同名站）。
    """
    hay = (str(title or "") + " " + str(snippet or "")).lower()
    if not hay.strip():
        return False
    phrases = [p for p in quoted_phrases(query) if _CJK.search(p)]
    for phrase in phrases:
        # 只对中文短语硬判：拉丁引号短语引擎侧已按短语处理，
        # 再逐字校验会把引擎的宽松匹配（分词、单复数）全部误杀
        if phrase.lower() not in hay:
            return False
    if phrases:
        # 调用方写引号就是要整串，已经出现了——按他要的精确判据算相关
        return True
    terms = entity_terms(query)
    if _CJK.search(query):
        blocks = content_blocks(query)
        if blocks:
            return any(_block_hit(b, hay, whole=require_adjacent) for b in blocks)
        if terms:
            # 中文侧全是泛词（「AstrBot 项目 开源」）→ 实体词照旧全部命中
            return all(latin_hit(t, hay) for t in terms)
        return False
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


def fuse_engines(items: list[dict]) -> list[dict]:
    """同一 URL 被多家引擎给出时合成一条：engines 记下都有谁给过。

    用「几家引擎都挑中同一个 URL」当共识信号——单家引擎的前 10 名只是一次抽样，
    两家独立引擎同时给出比任何单一权重都可靠。不做 RRF 的倒数排名累加：
    每个引擎只取 10 条、名次本身就是抽样噪声，有界的共识加分更稳（见 score）。
    """
    by_url: dict[str, dict] = {}
    out: list[dict] = []
    for item in items:
        key = _norm_url(item.get("url", "")) or f"_nourl_{len(out)}"
        first = by_url.get(key)
        if first is None:
            merged = dict(item)
            provider = str(item.get("provider") or "")
            merged["engines"] = [provider] if provider else []
            by_url[key] = merged
            out.append(merged)
            continue
        provider = str(item.get("provider") or "")
        if provider and provider not in first["engines"]:
            first["engines"].append(provider)
        # 摘要取更全的那份：RSS 给的 description 长短不一，短的那份常是半句话
        if len(str(item.get("snippet") or "")) > len(str(first.get("snippet") or "")):
            first["snippet"] = item["snippet"]
    return out


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
    """实体命中 0.55 + 域名权威 0.30 + 新近 0.15 + 多引擎共识（每多一家 +0.12，最多 +0.24）。

    实体仍占大头——先看是不是它，再看站可不可信；共识只做加分项，
    不让两家引擎一起给出的无关页翻上来（闸门已经在前面把它们拦掉了）。
    """
    terms = [str(t).lower() for t in (entity_terms or []) if str(t).strip()]
    hay = (str(item.get("title", "")) + " " + str(item.get("snippet", ""))).lower()
    if terms:
        entity_score = sum(1 for t in terms if latin_hit(t, hay)) / len(terms)
    else:
        entity_score = 0.5
    domain = item.get("domain") or domain_of(item.get("url", ""))
    consensus = max(len(item.get("engines") or []) - 1, 0)
    return (0.55 * entity_score + 0.30 * authority(domain, entity_terms)
            + 0.15 * _recency(item.get("published_at")) + 0.12 * min(consensus, 2))


def rank(items: list[dict], *, entity_terms=(), per_domain: int = 3, limit: int = 8) -> list[dict]:
    """融合多引擎来源 -> 去重 -> 打分排序 -> 每域名限量 -> 截断。每域名限量是为了不让一个站刷满整页。"""
    unique = dedupe(fuse_engines(list(items)))
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
