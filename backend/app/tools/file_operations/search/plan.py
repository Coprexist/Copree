"""查询规划：把一条自然语言问题变成一组可执行的检索式。

语言层面的规划（挑哪个实体、要不要英文说法、限定哪一年）留给调用方模型——它本来就在场，
工具内部再调一次模型只会多一次往返和一份 token。这里只做不依赖语义、可验证的机械变形，
保证调用方只给原话时也能兜住。

机械变形能到哪一步（以「Copree（AIsChat）类似平台的发展和发布」为例）：
去括号标点 -> Copree AIsChat；拆粘连词 -> Copree AIsChat；拉丁词元 -> Copree / AIsChat。
到不了「AI agent group chat platform」——那是语义改写，归模型。
"""
from __future__ import annotations

import re

MAX_QUERIES = 4    # 模型一次能给的检索式条数上限（与工具 schema 同一口径）
MAX_VARIANTS = 6   # 机械改写最多派生几条候选，回退阶梯按这个顺序走

_BRACKETS = "（）()【】[]「」『』《》<>"
_QUOTES = "\"'“”‘’"
_PUNCT = "，。、；：！？,.;:!?~～|"
_GLUE = "-_/·—－"

_LATIN = re.compile(r"[A-Za-z][A-Za-z0-9]*")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

# 通用拉丁词：当实体词用会把任何页面都判成相关（查「AI 智能体」时所有 AI 站点都命中）
_LATIN_STOP = frozenset({
    "ai", "app", "web", "api", "os", "it", "to", "of", "the", "and", "for",
    "is", "in", "on", "at", "by", "or", "new", "top", "pro", "net", "com", "www",
})

# 来源标记：它们是「去哪找」而不是「找什么」。拿它们判相关性等于没判——
# github 出现在每个 GitHub 页面的标题里，实测会把三毛机场的仓库当成 AIsChat 的结果。
SOURCE_MARKERS = frozenset({
    "github", "gitee", "gitlab", "producthunt", "crunchbase", "hackernews",
    "wikipedia", "reddit", "twitter", "site", "docs", "blog", "changelog",
})


def _clean(text: str) -> str:
    """去括号与其内容边界外的标点、统一空白。括号只去符号不去内容（括号里常是别名）。"""
    out = str(text or "")
    for ch in _BRACKETS + _QUOTES + _PUNCT:
        out = out.replace(ch, " ")
    for ch in _GLUE:
        out = out.replace(ch, " ")
    return re.sub(r"\s+", " ", out).strip()


def _split_glued(text: str) -> str:
    """CopreeAIsChat 与 copreeAIsChat 这类粘连词切开：Copree AIsChat。"""
    out = _CAMEL.sub(" ", text)
    return re.sub(r"\s+", " ", out).strip()


def latin_terms(text: str) -> list[str]:
    """文本里的拉丁词元（去重保序，长度 >= 2）——品牌名、产品名基本都在这。"""
    seen: list[str] = []
    for m in _LATIN.findall(_clean(text)):
        if len(m) >= 2 and m.lower() not in _LATIN_STOP and m.lower() not in [s.lower() for s in seen]:
            seen.append(m)
    return seen


def entity_terms(text: str) -> list[str]:
    """实体词 = 拉丁词元减去来源标记。判相关性只认实体词，来源标记只算加分项。"""
    return [t for t in latin_terms(text) if t.lower() not in SOURCE_MARKERS]


def plan_queries(raw: str, *, max_variants: int = MAX_VARIANTS) -> list[str]:
    """原话到候选检索式，按优先级排序、去重。

    顺序是有意的：先完整短语（最精确），再切开粘连词，再只留拉丁词元的合并式，
    最后才是单个词元——单个词元噪音最大，只在前面的都搜不到时才有机会被用到。
    """
    base = _clean(raw)
    if not base:
        return []
    variants: list[str] = [base]

    split = _split_glued(base)
    if split != base:
        variants.append(split)

    terms = latin_terms(base)
    if len(terms) >= 2:
        joined = " ".join(terms)
        if joined not in variants:
            variants.append(joined)
        variants.extend(t for t in terms if t not in variants)

    out: list[str] = []
    for v in variants:
        if v and v not in out:
            out.append(v)
        if len(out) >= max_variants:
            break
    return out


def merge_queries(given, fallback_raw: str) -> list[str]:
    """调用方给的检索式优先，不足时用机械改写补齐（上层只给原话也能兜住）。"""
    out: list[str] = []
    for q in list(given or []):
        q = _clean(q)
        if q and q not in out:
            out.append(q)
        if len(out) >= MAX_QUERIES:
            return out
    for q in plan_queries(fallback_raw):
        if q not in out:
            out.append(q)
        if len(out) >= MAX_VARIANTS:
            break
    return out
