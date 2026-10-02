"""聚类、分类与规则降级。

两件事：
1. 把「同一事件的多条报道」合成一个候选（Cluster）——这是省 token 的关键：
   15 个源每天给回几百条，同事件重复率很高，先本地合并再送 AI，调用量能降一个数量级。
2. AI 不可用时（没填 key / 欠费 / 超时）用规则产出可读卡片，保证屏幕永远有内容。

多源报道本身就是「重要」的强信号：一条新闻被 6 个源同时报道，几乎必定是大事件。
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import logging
import math
import re
from dataclasses import dataclass, field

from . import textutil
from .models import NewsCard, RawItem

# 作文角度库：按「关键词优先、分类兜底」挑选。课堂上学生看到的不只是新闻，
# 还有这条新闻能撑起什么论点，这是把它放在教室里的主要理由。
_ANGLE_BY_KEYWORD: list[tuple[tuple[str, ...], str]] = [
    (("人工智能", "大模型", "芯片", "半导体", "算力", "算法"),
     "科技自立：核心技术买不来，只能自己长出来"),
    (("航天", "火箭", "卫星", "空间站", "探月", "探测器"),
     "长期主义：大工程靠一代人持续投入"),
    (("高考", "中考", "教育", "学生", "教师", "校园", "双减"),
     "教育公平：机会均等才有社会活力"),
    (("医保", "养老", "补贴", "民生", "社保", "住房", "物价"),
     "民生温度：政策的落点在普通人的日常里"),
    (("地震", "台风", "洪水", "暴雨", "救援", "山火", "灾"),
     "守望相助：危难时刻的责任与担当"),
    (("夺冠", "冠军", "纪录", "比赛", "奥运", "亚运", "决赛"),
     "超越自我：竞技场上的意志与坚持"),
    (("研究", "论文", "科学家", "发现", "实验", "观测"),
     "坐冷板凳：基础研究需要耐心与好奇心"),
    (("环保", "生态", "碳", "污染", "新能源", "气候"),
     "人与自然：发展不能透支未来"),
    (("就业", "创业", "经济", "消费", "乡村", "振兴"),
     "实干与机会：青年与时代的双向奔赴"),
    (("文化", "非遗", "传统", "文物", "考古", "博物馆"),
     "文化自信：让传统活在当下"),
    (("野生", "动物", "大熊猫", "候鸟", "物种", "濒危", "罕见", "奇观", "鲸", "雪豹"),
     "敬畏自然：与万物共处的方式"),
    (("医疗", "医生", "医院", "疫苗", "药物", "手术", "病房", "公共卫生"),
     "专业与良知：把本事用在别人需要的地方"),
]

_ANGLE_BY_CATEGORY: dict[str, list[str]] = {
    "top": [
        "家国与集体：个体选择如何汇成公共力量",
        "理性看待突发事件：信息与判断同样重要",
        "责任与担当：关键岗位上的普通人",
    ],
    "politics": [
        "规则与秩序：制度进步如何改变生活",
        "小事与大局：民生议题里的公共关怀",
        "参与与表达：青年如何介入公共生活",
    ],
    "tech": [
        "创新驱动：技术如何改变普通人的一天",
        "科技向善：能力越强越需要边界",
        "好奇心与探索：从问题到答案的距离",
    ],
    "novelty": [
        "平凡中的不平凡：普通人的选择也发光",
        "偶然与必然：有趣发现背后往往是长期准备",
        "换个角度看世界：常识之外还有天地",
    ],
}

_HOT_RE = re.compile(r"(\d[\d,\.]*)\s*万?")


def _hot_score(extra: str) -> float:
    """热搜榜的热度值转成 0~3 的加分。热度跨数量级，取对数压缩。"""
    if not extra:
        return 0.0
    match = _HOT_RE.search(extra)
    if not match:
        return 0.0
    raw = match.group(1).replace(",", "").rstrip(".")
    try:
        value = float(raw)
    except ValueError:
        return 0.0
    if "万" in extra:
        value *= 10000
    if value <= 1:
        return 0.0
    return min(3.0, math.log10(value) / 2.0)


def _keyword_hits(text: str, keywords: list[str]) -> int:
    return sum(1 for kw in keywords if kw and kw in text)


@dataclass
class Cluster:
    """一个候选事件：可能只有一条报道，也可能是十几条同事件报道的合并。"""

    cid: int
    title: str
    category: str
    hint: str
    sources: list[str] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)
    items: list[RawItem] = field(default_factory=list)
    summary: str = ""
    published: _dt.datetime | None = None
    score: float = 0.0

    @property
    def item_count(self) -> int:
        return len(self.items)


def guess_category(item: RawItem, categories: list[dict]) -> str:
    """按关键词给单条新闻初判分类；没命中就用来源提示，再没有就落到时政。"""
    text = f"{item.title} {item.summary}"
    best_key, best_score = "", 0
    for cat in categories:
        hits = _keyword_hits(text, list(cat.get("keywords") or []))
        if hits > best_score:
            best_key, best_score = cat["key"], hits
    if best_key:
        return best_key
    valid = {c["key"] for c in categories}
    if item.hint in valid:
        return item.hint
    return "politics" if "politics" in valid else (categories[0]["key"] if categories else "politics")


def _medoid_title(items: list[RawItem]) -> str:
    """选出「最能代表这一簇」的标题：与簇内其他标题平均相似度最高的那个。

    直接取第一条会让标题取决于抓取顺序（热搜词常常很短），取中位数标题更稳。
    """
    if len(items) == 1:
        return items[0].title
    titles = [i.title for i in items][:12]  # 限制规模，避免 O(n^2) 在大簇上变慢
    best, best_score = titles[0], -1.0
    for cand in titles:
        score = sum(textutil.similarity(cand, other) for other in titles if other is not cand)
        # 同样相似度时偏向信息量更大的长标题
        score += len(cand) / 1000.0
        if score > best_score:
            best, best_score = cand, score
    return best


def cluster_items(items: list[RawItem], categories: list[dict], logger: logging.Logger,
                  threshold: float = 0.42) -> list[Cluster]:
    """把同事件条目合并。贪心单遍聚类，规模小（几百条）足够用。

    每个新条目只与簇的「代表标题」（首个条目）比较，不做簇间传递比较——
    否则阈值调低后 A~B、B~C 会把两件不相关的事链成一簇。
    """
    clusters: list[Cluster] = []
    for item in items:
        target = None
        for cluster in clusters:
            if textutil.is_same_event(item.title, cluster.title, threshold):
                target = cluster
                break
        if target is None:
            clusters.append(Cluster(
                cid=len(clusters) + 1,
                title=item.title,
                category=guess_category(item, categories),
                hint=item.hint,
                sources=[item.source],
                urls=[item.url],
                items=[item],
                summary=item.summary,
                published=item.published,
            ))
        else:
            target.items.append(item)
            if item.source and item.source not in target.sources:
                target.sources.append(item.source)
            if item.url:
                target.urls.append(item.url)
            # 摘要取最长的：短摘要常常只是标题复述，长的那条信息量大
            if len(item.summary) > len(target.summary):
                target.summary = item.summary
            if item.published and (target.published is None or item.published > target.published):
                target.published = item.published

    now_ts = textutil.now_utc().timestamp()
    for cluster in clusters:
        cluster.title = _medoid_title(cluster.items)
        # 合并后分类可能变了：单条标题可能不含关键词，多条合并后信息量更大，重判一次
        merged_text = f"{cluster.title} {cluster.summary} " + " ".join(i.title for i in cluster.items[:6])
        best_key, best_hits = cluster.category, 0
        for cat in categories:
            hits = _keyword_hits(merged_text, list(cat.get("keywords") or []))
            if hits > best_hits:
                best_key, best_hits = cat["key"], hits
        cluster.category = best_key

        published_ts = cluster.published.timestamp() if cluster.published else 0
        # 越新越靠前，36 小时之外不再加分（避免陈年条目靠多源数压过今天的新闻）
        recency = max(0.0, 1.0 - (now_ts - published_ts) / (36 * 3600)) if published_ts else 0.3
        cluster.score = (
            1.0
            + 1.6 * (len(cluster.sources) - 1)          # 多源报道 = 重要
            + max(_hot_score(i.extra) for i in cluster.items)  # 榜单热度
            + recency
            + (0.8 if cluster.category == "top" else 0.0)
        )
    logger.info("聚类完成：%d 条 -> %d 个候选事件", len(items), len(clusters))
    return clusters


def angle_for(cluster: Cluster) -> str:
    text = f"{cluster.title} {cluster.summary}"
    for keywords, angle in _ANGLE_BY_KEYWORD:
        if any(kw in text for kw in keywords):
            return angle
    pool = _ANGLE_BY_CATEGORY.get(cluster.category) or _ANGLE_BY_CATEGORY["politics"]
    # 按指纹挑选，保证同一条新闻每次显示的角度稳定
    idx = int(hashlib.sha1(cluster.title.encode("utf-8", "ignore")).hexdigest(), 16) % len(pool)
    return pool[idx]


def heuristic_card(cluster: Cluster) -> NewsCard:
    """规则降级卡片：标题用代表标题，概括用最长原文摘要（没有就说明只有词条）。"""
    summary = cluster.summary
    if not summary:
        if len(cluster.sources) > 1:
            summary = f"{'、'.join(cluster.sources[:4])} 等 {len(cluster.sources)} 个来源同时在报道这一条。"
        else:
            summary = "来自热榜的简要词条，点开可查看完整报道。"
    return NewsCard(
        title=cluster.title,
        summary=summary,
        angle=angle_for(cluster),
        category=cluster.category,
        sources=list(cluster.sources),
        urls=list(cluster.urls),
        published=cluster.published,
        item_count=cluster.item_count,
        score=cluster.score,
        from_ai=False,
    )
