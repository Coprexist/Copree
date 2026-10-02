"""新闻采集：RSS/Atom 解析 + 国内热搜榜 JSON 适配器 + 并发抓取。

一个源坏掉不能拖垮整轮抓取，所以每个源独立 try/except，失败只记状态，界面底部会列出
「失败源」，方便一眼看出是全网断网还是某个源改版。
"""

from __future__ import annotations

import datetime as _dt
import logging
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import netclient, textutil
from .config import active_sources
from .models import RawItem, SourceStatus

# 热搜榜没有发布时间字段：它的语义就是「此刻在榜」，因此按抓取时刻计时，
# 这样它们总能落进「上次到这次」的时间窗口，再由指纹去重保证不重复推送。
_NOW = lambda: textutil.now_utc()  # noqa: E731


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower() if isinstance(tag, str) else ""


def _child_text(node: ET.Element, *names: str) -> str:
    for child in list(node):
        if _localname(child.tag) in names:
            text = (child.text or "").strip()
            if text:
                return text
            # content:encoded 之类可能套了一层
            nested = "".join(child.itertext()).strip()
            if nested:
                return nested
    return ""


def _atom_link(node: ET.Element) -> str:
    fallback = ""
    for child in list(node):
        if _localname(child.tag) != "link":
            continue
        href = child.get("href") or ""
        rel = (child.get("rel") or "alternate").lower()
        if child.text and child.text.strip().startswith("http"):
            href = child.text.strip()
        if not href:
            continue
        if rel == "alternate":
            return href
        fallback = fallback or href
    return fallback


def parse_feed(xml_text: str, source: dict) -> list[RawItem]:
    """解析 RSS 2.0 / Atom。字段名大小写与命名空间前缀千差万别，统一按 local-name 匹配。"""
    if not xml_text or not xml_text.strip():
        raise ValueError("响应为空")
    # 有些源会在 XML 前面塞 BOM 或 BOM+空白
    xml_text = xml_text.lstrip("\ufeff \r\n\t")
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError(f"XML 解析失败：{exc}") from exc

    # 源站改版或触发反爬时会返回一个合法但无关的 XML/HTML，明确报出来才能一眼看出是哪种故障
    if _localname(root.tag) not in ("rss", "feed", "rdf"):
        raise ValueError(f"响应不是 RSS/Atom 文档（根节点 {_localname(root.tag) or '未知'}，可能被反爬拦截）")

    nodes = [n for n in root.iter() if _localname(n.tag) in ("item", "entry")]
    items: list[RawItem] = []
    for node in nodes:
        title = textutil.clean_text(_child_text(node, "title"), 200)
        if not title:
            continue
        link = _child_text(node, "link") or _atom_link(node)
        summary = textutil.clean_text(
            _child_text(node, "description", "summary", "encoded", "content"), 400)
        published = textutil.parse_time(
            _child_text(node, "pubdate", "published", "updated", "date", "created"))
        items.append(RawItem(
            title=title,
            url=link.strip(),
            source=source.get("name", ""),
            hint=source.get("hint", ""),
            summary=summary,
            published=published,
        ))
    return items


# ---------------------------------------------------------------- 热搜榜适配器

def parse_baidu_hot(payload, source: dict) -> list[RawItem]:
    """百度热搜结构是 cards[].content[].content[]（榜单分组下还有一层），两层都兼容。"""
    items: list[RawItem] = []
    entries: list[dict] = []
    for card in ((payload or {}).get("data") or {}).get("cards") or []:
        for section in card.get("content") or []:
            if isinstance(section, dict) and isinstance(section.get("content"), list):
                entries.extend(e for e in section["content"] if isinstance(e, dict))
            elif isinstance(section, dict):
                entries.append(section)
    for entry in entries:
        word = textutil.clean_text(entry.get("word") or entry.get("query") or "", 200)
        if not word:
            continue
        hot = entry.get("hotScore") or entry.get("hotChange") or ""
        items.append(RawItem(
            title=word,
            url=entry.get("url") or entry.get("rawUrl") or "",
            source=source.get("name", ""),
            hint=source.get("hint", "top"),
            summary=textutil.clean_text(entry.get("desc") or "", 300),
            published=_NOW(),
            extra=f"热度 {hot}" if hot else "",
        ))
    return items


def parse_toutiao_hot(payload, source: dict) -> list[RawItem]:
    items: list[RawItem] = []
    for entry in (payload or {}).get("data") or []:
        title = textutil.clean_text(entry.get("Title") or "", 200)
        if not title:
            continue
        hot = entry.get("HotValue") or ""
        items.append(RawItem(
            title=title,
            url=entry.get("Url") or "",
            source=source.get("name", ""),
            hint=source.get("hint", "top"),
            published=_NOW(),
            extra=f"热度 {hot}" if hot else "",
        ))
    return items


def parse_weibo_hot(payload, source: dict) -> list[RawItem]:
    """微博热搜需要登录 Cookie，默认关闭；这里只做结构解析。"""
    items: list[RawItem] = []
    band = (payload or {}).get("data") or {}
    for entry in band.get("realtime") or []:
        word = textutil.clean_text(entry.get("word") or entry.get("note") or "", 200)
        if not word:
            continue
        hot = entry.get("raw_hot") or entry.get("num") or ""
        items.append(RawItem(
            title=word,
            url="https://s.weibo.com/weibo?q=" + netclient.quote(word),
            source=source.get("name", ""),
            hint=source.get("hint", "top"),
            published=_NOW(),
            extra=f"热度 {hot}" if hot else "",
        ))
    return items


def parse_zhihu_daily(payload, source: dict) -> list[RawItem]:
    items: list[RawItem] = []
    for key in ("top_stories", "stories"):
        for entry in (payload or {}).get(key) or []:
            title = textutil.clean_text(entry.get("title") or "", 200)
            if not title:
                continue
            items.append(RawItem(
                title=title,
                url=entry.get("url") or (f"https://daily.zhihu.com/story/{entry.get('id')}"
                                         if entry.get("id") else ""),
                source=source.get("name", ""),
                hint=source.get("hint", "novelty"),
                summary=textutil.clean_text(entry.get("hint") or "", 200),
                published=_NOW(),
            ))
    return items


_JSON_ADAPTERS = {
    "baidu_hot": parse_baidu_hot,
    "toutiao_hot": parse_toutiao_hot,
    "weibo_hot": parse_weibo_hot,
    "zhihu_daily": parse_zhihu_daily,
}


def fetch_source(source: dict, app_cfg: dict, logger: logging.Logger) -> tuple[list[RawItem], SourceStatus]:
    name = source.get("name") or source.get("url", "?")
    timeout = float(app_cfg.get("http_timeout", 15))
    retries = int(app_cfg.get("http_retries", 2))
    insecure = bool(app_cfg.get("allow_insecure_fallback"))
    proxy = (app_cfg.get("proxy") or "").strip() or None
    headers = dict(source.get("headers") or {})
    stype = (source.get("type") or "rss").lower()
    try:
        if stype == "rss":
            text = netclient.fetch_text(source["url"], timeout=timeout, retries=retries,
                                        headers=headers, allow_insecure_fallback=insecure, proxy=proxy)
            items = parse_feed(text, source)
        elif stype in _JSON_ADAPTERS:
            payload = netclient.fetch_json(source["url"], timeout=timeout, retries=retries,
                                           headers=headers, allow_insecure_fallback=insecure, proxy=proxy)
            items = _JSON_ADAPTERS[stype](payload, source)
        else:
            raise ValueError(f"未知来源类型：{stype}")

        if not items:
            return [], SourceStatus(name=name, ok=False, items=0, error="无有效条目（可能已改版）")
        logger.info("采集成功 %s：%d 条", name, len(items))
        return items, SourceStatus(name=name, ok=True, items=len(items))
    except Exception as exc:  # 单个源失败是常态，绝不能中断整轮
        logger.warning("采集失败 %s：%s", name, exc)
        return [], SourceStatus(name=name, ok=False, items=0, error=str(exc)[:120])


def fetch_all(cfg: dict, logger: logging.Logger) -> tuple[list[RawItem], list[SourceStatus]]:
    """并发抓取全部启用的源，返回 (条目, 每个源的状态)。"""
    sources = active_sources(cfg)
    if not sources:
        return [], []
    app_cfg = cfg.get("app", {})
    workers = max(1, min(int(app_cfg.get("fetch_workers", 8)), len(sources)))
    all_items: list[RawItem] = []
    statuses: list[SourceStatus] = []
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="fetch") as pool:
        futures = {pool.submit(fetch_source, s, app_cfg, logger): s for s in sources}
        for fut in as_completed(futures):
            try:
                items, status = fut.result()
            except Exception as exc:  # 理论上 fetch_source 已兜住，这里再兜一层
                src = futures[fut]
                items, status = [], SourceStatus(src.get("name", "?"), False, 0, str(exc)[:120])
            all_items.extend(items)
            statuses.append(status)

    # 同一源内的重复标题先压掉，跨源重复留给流水线的聚类处理
    deduped: dict[str, RawItem] = {}
    for item in all_items:
        deduped.setdefault(item.fingerprint, item)
    statuses.sort(key=lambda s: (not s.ok, s.name))
    return list(deduped.values()), statuses
