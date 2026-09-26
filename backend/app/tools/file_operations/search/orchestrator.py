"""检索编排：检索式 -> 多后端并行 -> 相关性闸门 -> 去重重排 -> 回退阶梯。

回退阶梯（只在上一级真的 0 条相关结果时往下走）：
  1. 主后端并行跑调用方给的检索式；
  2. 机械改写补出来的候选（去标点 / 拆粘连词 / 单拉丁词元）；
  3. 其余后端重试这些检索式。
单次调用对外请求数有上限：一次工具调用不该把网络打满，也不该把延迟堆到分钟级。

刻意不做的事：不抓站内页、不猜域名、不替调用方翻译。
抓页面是 web_fetch 的职责（工具返回 hint 让模型自己决定），猜域名会撞上无关站点，
翻译是语言活——调用方模型本来就在场，把英文说法直接放进 queries 比工具里再调一次模型便宜。
"""
from __future__ import annotations

import asyncio
import logging
import time

import httpx

from app.tools.file_operations.search.backends import BACKENDS, HEADERS, stamp
from app.tools.file_operations.search.plan import entity_terms, merge_queries
from app.tools.file_operations.search.rank import cjk_bigrams, rank, relevant

logger = logging.getLogger(__name__)

MAX_REQUESTS = 6      # 单次工具调用的对外请求上限（含回退）
TIMEOUT = 12.0
DEADLINE = 8.0        # 总时长上限：回退是为了救场，不该把一个对话轮次拖到十几秒
PER_REQUEST = 10      # 每个请求多取一点，重排后才有得挑

HINT = (
    "没有找到与该实体字面相关的公开结果——搜索引擎对冷门词会返回无关填充，已按相关性过滤。"
    "下一步建议：向用户索要官网 / GitHub / Product Hunt / X 的链接；拿到链接后用 web_fetch 打开，"
    "官网可先试 /sitemap.xml、/blog、/changelog、/docs、/release-notes 这几个路径。"
)


class _Budget:
    """共享请求预算。并发下用锁守住计数，避免回退把请求数放大到不可控。"""

    def __init__(self, total: int) -> None:
        self.left = total
        self._lock = asyncio.Lock()

    async def take(self) -> bool:
        async with self._lock:
            if self.left <= 0:
                return False
            self.left -= 1
            return True


def _with_excludes(query: str, exclude) -> str:
    """负向词两头都做：这里拼进引擎查询，结果侧再按标题摘要过滤一次。"""
    suffix = "".join(f" -{t}" for t in exclude if t)
    return f"{query}{suffix}"


def _excluded(item: dict, exclude) -> bool:
    hay = (str(item.get("title", "")) + " " + str(item.get("snippet", ""))).lower()
    return any(str(t).lower() in hay for t in exclude if str(t).strip())


async def _fetch_one(client, backend, query: str, count: int, exclude, budget: _Budget,
                   *, require_adjacent: bool = False):
    """一个后端 + 一条检索式。返回 (通过相关性闸门的结果, 失败原因)。

    失败或整页解析不出东西时重试一次：同一个请求前后几秒拿到不同结果是实测过的
    （同一 URL、同一份代码，一次给 8 条正确结果，一次空手），重试比换引擎便宜。
    只在「传输出错 / 一条都没解析出来」时重试；解析出来但不相关的不重试——
    那是引擎对这条查询的真实看法。
    """
    last = None
    parsed = 0
    for _ in range(2):
        if not await budget.take():
            break
        try:
            resp = await client.get(
                backend.url(_with_excludes(query, exclude), count),
                headers=HEADERS, timeout=TIMEOUT, follow_redirects=True,
            )
            if resp.status_code >= 400:
                last = f"{backend.name} HTTP {resp.status_code}"
                continue
            items = stamp(backend.parse(resp.text, count), backend)
            parsed = len(items)
            kept = [i for i in items if relevant(query, i.get("title", ""), i.get("snippet", ""),
                                                 require_adjacent=require_adjacent)]
            for item in kept:
                item["matched_query"] = query
            if items or kept:
                return kept, None, _stat(backend, query, parsed, len(kept))
            last = None
        except Exception as e:
            logger.debug("搜索后端失败 %s: %s", backend.name, e)
            last = f"{backend.name} {type(e).__name__}"
            parsed = 0
    return [], last, _stat(backend, query, parsed, 0, last)


def _stat(backend, query: str, parsed: int, kept: int, error: str | None = None) -> dict:
    """一轮的实况：解析出几条、过闸几条、为什么没成。

    没有这份记录时，「搜不到」只能靠猜（引擎降级？解析变了？还是真没有）——
    结果里带上它，模型和排查的人都能直接看出是哪一种。
    """
    stat = {"provider": backend.name, "query": query, "parsed": parsed, "kept": kept}
    if error:
        stat["error"] = error
    return stat


async def _round(client, backend, queries, exclude, budget: _Budget,
                 machine_from: int = 0) -> tuple[list[dict], list[str], list[dict]]:
    """一个后端并行跑一批检索式。machine_from 之后的检索式是机械拆词派生的，判相关性要更严。"""
    results = await asyncio.gather(*[
        _fetch_one(client, backend, q, PER_REQUEST, exclude, budget,
                   require_adjacent=i >= machine_from)
        for i, q in enumerate(queries)
    ])
    items: list[dict] = []
    failed: list[str] = []
    stats: list[dict] = []
    for got, err, stat in results:
        items.extend(got)
        stats.append(stat)
        if err:
            failed.append(err)
    return items, failed, stats


def entity_terms_of(query: str) -> list[str]:
    """打分用的实体词：有实体词就用它（品牌名基本都在这），纯中文查询退回二元组。"""
    return entity_terms(query) or sorted(cjk_bigrams(query))


async def search(queries, *, fallback_raw: str = "", exclude=(), limit: int = 8,
                 per_domain: int = 3) -> dict:
    """检索式进，排好序的结果出。任何一级空手而归都不会让整次调用变成「没搜到」就结束。"""
    raw = fallback_raw or (queries[0] if queries else "")
    given = {str(q).strip() for q in (queries or []) if str(q).strip()}
    candidates = merge_queries(queries, raw)
    # 机械拆词派生的候选排在调用方给的那些之后，这里数出分界点
    machine_from = 0
    for name in candidates:
        if name in given:
            machine_from += 1
        else:
            break
    exclude = [str(t).strip() for t in (exclude or []) if str(t).strip()]
    if not candidates:
        return {"success": False, "error": "没有可用的检索式", "results": [], "count": 0}

    budget = _Budget(MAX_REQUESTS)
    started = time.monotonic()
    collected: list[dict] = []
    failed: list[str] = []
    attempts: list[dict] = []
    async with httpx.AsyncClient(follow_redirects=True, timeout=TIMEOUT) as client:
        collected, failed, stats = await _round(
            client, BACKENDS[0], candidates, exclude, budget, machine_from)
        attempts.extend(stats)
        for backend in BACKENDS[1:]:
            if collected or budget.left <= 0:
                break
            if time.monotonic() - started > DEADLINE:
                break
            more, errors, stats = await _round(
                client, backend, candidates, exclude, budget, machine_from)
            failed.extend(errors)
            attempts.extend(stats)
            collected = more
    logger.info("搜索实况 %s", [(a["provider"], a["query"][:24], a["parsed"], a["kept"]) for a in attempts])

    kept = [i for i in collected if not _excluded(i, exclude)]
    ranked = rank(kept, entity_terms=entity_terms_of(candidates[0]),
                  per_domain=per_domain, limit=limit)
    out = {
        "success": True,
        "queries": candidates,
        "provider_used": sorted({i.get("provider", "") for i in ranked if i.get("provider")}),
        "count": len(ranked),
        "results": ranked,
        "attempts": attempts,
    }
    if failed:
        out["failed"] = failed
    if not ranked:
        out["hint"] = HINT
    return out
