"""数据结构：采集层与处理层之间只走这几个对象，便于离线测试。

全部用 dataclass 而非裸 dict：字段名写错时立刻报错，而不是在渲染阶段才发现卡片空白。
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field

from . import textutil


@dataclass
class RawItem:
    """一条原始新闻条目（可能只是一个热搜词，所以摘要允许为空）。"""

    title: str
    url: str = ""
    source: str = ""
    hint: str = ""          # 来源自带的分类倾向：top/politics/tech/novelty
    summary: str = ""
    published: _dt.datetime | None = None
    extra: str = ""         # 热搜榜的热度值之类的附加信息
    fingerprint: str = ""

    def __post_init__(self) -> None:
        self.title = textutil.clean_text(self.title, 200)
        self.summary = textutil.clean_text(self.summary, 600)
        if not self.fingerprint:
            self.fingerprint = textutil.fingerprint(self.title, self.url)

    @property
    def has_time(self) -> bool:
        return self.published is not None

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "url": self.url,
            "source": self.source,
            "hint": self.hint,
            "summary": self.summary,
            "published": textutil.iso(self.published),
            "extra": self.extra,
            "fingerprint": self.fingerprint,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RawItem":
        return cls(
            title=data.get("title", ""),
            url=data.get("url", ""),
            source=data.get("source", ""),
            hint=data.get("hint", ""),
            summary=data.get("summary", ""),
            published=textutil.parse_time(data.get("published")),
            extra=data.get("extra", ""),
            fingerprint=data.get("fingerprint", ""),
        )


@dataclass
class NewsCard:
    """最终上屏的一张卡片：一条可读的新闻（可能由多条同事件报道合并而来）。"""

    title: str
    summary: str = ""
    angle: str = ""                 # 作文可用角度 / 立意提示
    category: str = "politics"      # 分类 key
    sources: list[str] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)
    published: _dt.datetime | None = None
    item_count: int = 1
    score: float = 0.0              # 越大越靠前（多源报道、有新意都加分）
    fingerprint: str = ""
    from_ai: bool = False

    def __post_init__(self) -> None:
        self.title = textutil.clean_text(self.title, 120)
        self.summary = textutil.clean_text(self.summary, 600)
        self.angle = textutil.clean_text(self.angle, 120)
        # 去重但保持顺序：同一来源可能有多条 URL 指向同一事件
        self.sources = list(dict.fromkeys(s for s in self.sources if s))
        self.urls = list(dict.fromkeys(u for u in self.urls if u))
        if not self.fingerprint:
            self.fingerprint = textutil.fingerprint(self.title, self.urls[0] if self.urls else "")

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "summary": self.summary,
            "angle": self.angle,
            "category": self.category,
            "sources": self.sources,
            "urls": self.urls,
            "published": textutil.iso(self.published),
            "item_count": self.item_count,
            "score": round(self.score, 2),
            "fingerprint": self.fingerprint,
            "from_ai": self.from_ai,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "NewsCard":
        return cls(
            title=data.get("title", ""),
            summary=data.get("summary", ""),
            angle=data.get("angle", ""),
            category=data.get("category", "politics"),
            sources=list(data.get("sources") or []),
            urls=list(data.get("urls") or []),
            published=textutil.parse_time(data.get("published")),
            item_count=int(data.get("item_count") or 1),
            score=float(data.get("score") or 0.0),
            fingerprint=data.get("fingerprint", ""),
            from_ai=bool(data.get("from_ai")),
        )


@dataclass
class CategoryBlock:
    key: str
    label: str
    accent: str
    cards: list[NewsCard] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"key": self.key, "label": self.label, "accent": self.accent,
                "cards": [c.to_dict() for c in self.cards]}


@dataclass
class SourceStatus:
    name: str
    ok: bool
    items: int = 0
    error: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "ok": self.ok, "items": self.items, "error": self.error}


@dataclass
class RunResult:
    """一次抓取 + 概括的完整结果，既喂给界面，也落盘成 latest.json。"""

    generated_at: _dt.datetime
    window_start: _dt.datetime | None = None
    window_end: _dt.datetime | None = None
    blocks: list[CategoryBlock] = field(default_factory=list)
    source_status: list[SourceStatus] = field(default_factory=list)
    fetched: int = 0            # 抓到的原始条目数
    kept: int = 0               # 落在时间窗口内、去重后的条目数
    ai_used: bool = False
    ai_error: str = ""
    ai_cards: int = 0
    duration: float = 0.0
    note: str = ""

    @property
    def sources_ok(self) -> int:
        return sum(1 for s in self.source_status if s.ok)

    @property
    def sources_failed(self) -> int:
        return sum(1 for s in self.source_status if not s.ok)

    @property
    def failed_names(self) -> list[str]:
        return [s.name for s in self.source_status if not s.ok]

    @property
    def card_count(self) -> int:
        return sum(len(b.cards) for b in self.blocks)

    def to_dict(self) -> dict:
        return {
            "generated_at": textutil.iso(self.generated_at),
            "window_start": textutil.iso(self.window_start),
            "window_end": textutil.iso(self.window_end),
            "blocks": [b.to_dict() for b in self.blocks],
            "source_status": [s.to_dict() for s in self.source_status],
            "fetched": self.fetched,
            "kept": self.kept,
            "ai_used": self.ai_used,
            "ai_error": self.ai_error,
            "ai_cards": self.ai_cards,
            "duration": round(self.duration, 2),
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RunResult":
        blocks = []
        for b in data.get("blocks") or []:
            blocks.append(CategoryBlock(
                key=b.get("key", ""), label=b.get("label", ""), accent=b.get("accent", ""),
                cards=[NewsCard.from_dict(c) for c in (b.get("cards") or [])],
            ))
        return cls(
            generated_at=textutil.parse_time(data.get("generated_at")) or textutil.now_utc(),
            window_start=textutil.parse_time(data.get("window_start")),
            window_end=textutil.parse_time(data.get("window_end")),
            blocks=blocks,
            source_status=[SourceStatus(s.get("name", ""), bool(s.get("ok")), int(s.get("items") or 0),
                                        s.get("error", "")) for s in (data.get("source_status") or [])],
            fetched=int(data.get("fetched") or 0),
            kept=int(data.get("kept") or 0),
            ai_used=bool(data.get("ai_used")),
            ai_error=data.get("ai_error", ""),
            ai_cards=int(data.get("ai_cards") or 0),
            duration=float(data.get("duration") or 0.0),
            note=data.get("note", ""),
        )

    @property
    def has_content(self) -> bool:
        return self.card_count > 0
