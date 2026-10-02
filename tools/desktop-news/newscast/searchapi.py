"""可选的搜索 API 通道（Tavily / 博查 / Serper / 自定义）。

RSS 已经覆盖日常需求，这一层是「RSS 没写但我今天就想搜一下」的补充：按关键词真正去搜，
拿回来的往往是聚合页或深度报道，正好补上大事件的背景。默认关闭，因为要额外花钱。
"""

from __future__ import annotations

import datetime as _dt
import logging

from . import netclient, textutil
from .models import RawItem


class SearchError(RuntimeError):
    pass


def _dig(payload, path: str):
    """按 "data.webPages.value" 这种点路径取嵌套字段。"""
    node = payload
    for part in str(path).split("."):
        if part == "":
            continue
        if isinstance(node, list):
            try:
                node = node[int(part)]
            except (ValueError, IndexError):
                return None
        elif isinstance(node, dict):
            node = node.get(part)
        else:
            return None
        if node is None:
            return None
    return node


def _tavily(cfg: dict, query: str, key: str, logger: logging.Logger) -> list[RawItem]:
    payload = {
        "api_key": key,
        "query": query,
        "topic": "news",
        "search_depth": "basic",
        "days": int(cfg.get("days", 2)),
        "max_results": int(cfg.get("results_per_query", 8)),
        "include_answer": False,
    }
    data = netclient.post_json("https://api.tavily.com/search", payload, timeout=45, retries=1)
    out = []
    for entry in (data or {}).get("results") or []:
        out.append(RawItem(
            title=entry.get("title") or "",
            url=entry.get("url") or "",
            source="搜索",
            summary=entry.get("content") or "",
            published=textutil.parse_time(entry.get("published_date")) or textutil.now_utc(),
        ))
    return out


def _bocha(cfg: dict, query: str, key: str, logger: logging.Logger) -> list[RawItem]:
    payload = {
        "query": query,
        "freshness": "oneWeek" if int(cfg.get("days", 1)) > 1 else "oneDay",
        "summary": True,
        "count": int(cfg.get("results_per_query", 8)),
    }
    data = netclient.post_json("https://api.bochaai.com/v1/web-search", payload, timeout=45, retries=1,
                              headers={"Authorization": f"Bearer {key}"})
    values = _dig(data, "data.webPages.value") or _dig(data, "webPages.value") or []
    out = []
    for entry in values:
        out.append(RawItem(
            title=entry.get("name") or "",
            url=entry.get("url") or "",
            source="搜索",
            summary=entry.get("summary") or entry.get("snippet") or "",
            published=textutil.parse_time(entry.get("datePublished")) or textutil.now_utc(),
        ))
    return out


def _serper(cfg: dict, query: str, key: str, logger: logging.Logger) -> list[RawItem]:
    payload = {"q": query, "num": int(cfg.get("results_per_query", 8)), "hl": "zh-cn", "gl": "cn"}
    data = netclient.post_json("https://google.serper.dev/news", payload, timeout=45, retries=1,
                              headers={"X-API-KEY": key})
    out = []
    for entry in (data or {}).get("news") or []:
        out.append(RawItem(
            title=entry.get("title") or "",
            url=entry.get("link") or "",
            source="搜索",
            summary=entry.get("snippet") or "",
            published=textutil.parse_time(entry.get("date")) or textutil.now_utc(),
        ))
    return out


def _custom(cfg: dict, query: str, key: str, logger: logging.Logger) -> list[RawItem]:
    """自定义服务商：配置里给出端点、请求体模板与结果字段路径。

    模板里 {query} 会被替换成实际关键词，{key} 替换成 API key，方便接自建搜索。
    """
    endpoint = cfg.get("endpoint") or ""
    if not endpoint:
        raise SearchError("search.endpoint 未配置")
    body = {}
    for k, v in (cfg.get("body") or {}).items():
        body[k] = str(v).replace("{query}", query).replace("{key}", key)
    headers = {k: str(v).replace("{key}", key) for k, v in (cfg.get("headers") or {}).items()}
    data = netclient.post_json(endpoint, body, timeout=45, retries=1, headers=headers)
    values = _dig(data, cfg.get("results_path") or "") or []
    if isinstance(values, dict):
        values = [values]
    out = []
    for entry in values:
        if not isinstance(entry, dict):
            continue
        out.append(RawItem(
            title=str(entry.get(cfg.get("title_field", "title")) or ""),
            url=str(entry.get(cfg.get("url_field", "url")) or ""),
            source="搜索",
            summary=str(entry.get(cfg.get("snippet_field", "snippet")) or ""),
            published=textutil.parse_time(entry.get(cfg.get("date_field", "date"))) or textutil.now_utc(),
        ))
    return out


_PROVIDERS = {"tavily": _tavily, "bocha": _bocha, "serper": _serper, "custom": _custom}


def search_all(cfg: dict, search_cfg: dict, api_key: str, logger: logging.Logger) -> list[RawItem]:
    """按配置的关键词列表逐个搜索；单个关键词失败不影响其他关键词。"""
    provider = (search_cfg.get("provider") or "tavily").lower()
    impl = _PROVIDERS.get(provider)
    if impl is None:
        logger.warning("未知搜索服务商：%s", provider)
        return []
    queries = [q for q in (search_cfg.get("queries") or []) if q]
    items: list[RawItem] = []
    for query in queries:
        try:
            got = impl(search_cfg, query, api_key, logger)
            logger.info("搜索「%s」得到 %d 条", query, len(got))
            items.extend(got)
        except Exception as exc:
            logger.warning("搜索「%s」失败：%s", query, exc)
    seen: dict[str, RawItem] = {}
    for item in items:
        if item.title:
            seen.setdefault(item.fingerprint, item)
    return list(seen.values())
