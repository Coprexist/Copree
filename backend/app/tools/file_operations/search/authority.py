"""搜索结果排序用的域名先验（唯一来源）。

同一实体的官网、官方仓库比聚合站与导航站可信；权重只在双方都相关时决定谁排前面，
不参与「是否相关」的判定，也不会让高权重域名绕过实体匹配。

域名主干与实体名相同（产物的官网 copree.ai 对实体 Copree）视为官网——这条是通用规则，
不写死任何具体产品的域名。
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

# 权重是序数不是概率：官方发布页 > 官方仓库 > 科技媒体 > 未知 > 聚合导航
AUTHORITY: dict[str, float] = {
    "producthunt.com": 0.90,   # 发布页：新产品最集中的一手来源
    "github.com": 0.85,
    "gitee.com": 0.85,
    "crunchbase.com": 0.80,
    "techcrunch.com": 0.70,
    "theverge.com": 0.70,
    "venturebeat.com": 0.70,
    "36kr.com": 0.70,
    "huxiu.com": 0.70,
    "jiqizhixin.com": 0.70,
    "qbitai.com": 0.70,
    "sspai.com": 0.70,
    "ithome.com": 0.70,
    "infoq.cn": 0.70,
}

# 聚合站与导航站：压到最低。平台观察到过整页都是这类结果、模型却当成答案的场景
NAVIGATION: frozenset[str] = frozenset({
    "hao123.com", "2345.com", "so.com", "sogou.com", "bing.com",
    "baidu.com", "sm.cn", "360.cn",
})

OFFICIAL_AUTHORITY = 1.0
NAVIGATION_AUTHORITY = 0.1
DEFAULT_AUTHORITY = 0.5

_NON_LABEL = re.compile(r"[^a-z0-9]")


def domain_of(url: str) -> str:
    """URL 到可比较的域名（小写、去 www.）。取不出域名返回空串。"""
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def _in_table(domain: str, table) -> bool:
    """精确匹配或子域匹配：a.b.github.com 也算 github.com。"""
    for known in table:
        if domain == known or domain.endswith("." + known):
            return True
    return False


def authority(domain: str, entity_terms=()) -> float:
    """域名的权威权重。官网判定优先于表——表里没有的产品名域名照样算官网。"""
    if not domain:
        return DEFAULT_AUTHORITY
    stem = _NON_LABEL.sub("", domain.split(".")[0])
    if stem:
        for term in entity_terms:
            if stem == _NON_LABEL.sub("", str(term).lower()):
                return OFFICIAL_AUTHORITY
    for known, weight in AUTHORITY.items():
        if domain == known or domain.endswith("." + known):
            return weight
    if _in_table(domain, NAVIGATION):
        return NAVIGATION_AUTHORITY
    return DEFAULT_AUTHORITY
