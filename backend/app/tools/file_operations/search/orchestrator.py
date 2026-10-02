"""检索编排：检索式 -> 多后端**并发** -> 相关性闸门 -> 去重融合排序 -> 换检索式再试。

一轮的形态（并发是有意的：串行阶梯下总时长是各引擎之和，实测最快也要 3~5 秒，
引擎挂住时能把一个对话轮次拖到二十秒；而它们的失败彼此独立）：

  1. 调用方的检索式**轮转铺开**到各后端，每家最多 PER_BACKEND 条——每条检索式至少被两家
     引擎搜过，既有共识信号，请求数又只有「每家都跑全部」的一半；
  2. 空手时按**原因**分岔：引擎自己没给东西（parsed=0 / 超时）就让那几家重跑同一批检索式；
     引擎给了但全被闸门拦下，就换机械改写补出的候选——「检索式本身不对」换引擎救不回来，
     反过来也一样。判据含糊的话，回退只是在烧请求。

单次调用对外请求 <=12，整次调用硬上限 8 秒（DEADLINE，到点就用手上已有的结果）。

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
from app.tools.file_operations.search.rank import cjk_bigrams, has_gate_signal, rank, relevant

logger = logging.getLogger(__name__)

MAX_REQUESTS = 12     # 单次调用的对外请求上限（含回退）：并发之后延迟不再是瓶颈，请求数才是成本
TIMEOUT = 4.0         # 单个请求超时：实测引擎正常 0.5~1.5 秒，挂到四秒就是抽风，
                      # 让它在这一轮里重试一次，好过拖到回退轮换后端重跑
SOFT_DEADLINE = 5.0   # 已有结果时的等待上限：并发是为了快，不是为了等齐
DEADLINE = 8.0        # 一条没过闸时的硬上限：到点就用手上已有的结果
PER_REQUEST = 10      # 每个请求多取一点，重排后才有得挑
PER_BACKEND = 2       # 一轮里一个后端最多跑几条：轮转铺开，每条检索式至少两家搜过


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
    """一个后端 + 一条检索式。返回 (通过相关性闸门的结果, 失败原因, 实况)。

    失败或整页解析不出东西时重试一次：同一个请求前后几秒拿到不同结果是实测过的
    （同一 URL、同一份代码，一次给 8 条正确结果，一次空手），重试比换引擎便宜。
    只在「传输出错 / 一条都没解析出来」时重试；解析出来但不相关的不重试——
    那是引擎对这条查询的真实看法。
    """
    last = None
    parsed = 0
    for _ in range(2):
        if not await budget.take():
            # 预算是共享的：这条没轮上就如实记下来，别让编排层把它当成「引擎没给」
            last = last or "预算用尽"
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


def _allocate(backends, queries, per_backend: int = PER_BACKEND) -> list[tuple]:
    """把检索式轮转铺开到各后端：每条至少被两家搜过，一个后端最多 per_backend 条。"""
    queries = list(queries)
    if not queries:
        return []
    plan: list[tuple] = []
    for i, backend in enumerate(backends):
        picked: list[str] = []
        for k in range(min(per_backend, len(queries))):
            q = queries[(i + k) % len(queries)]
            if q not in picked:
                picked.append(q)
        plan.extend((backend, q) for q in picked)
    return plan


def _passed(tasks) -> bool:
    """这批已完成的任务里有没有过闸的结果——决定还要不要等剩下的引擎。"""
    for task in tasks:
        if task.cancelled() or task.exception() is not None:
            continue
        got, _err, _stat = task.result()
        if got:
            return True
    return False


async def _fan_out(client, backends, queries, exclude, budget: _Budget, *,
                   require_adjacent: bool = False, deadline: float | None = None,
                   soft: float | None = None):
    """所有后端并发跑各自的检索式，分两段等。

    第一段等到 soft：这段结束时**只要手上已有过闸的结果就不再等剩下的引擎**——
    并发是为了快，等齐慢的那家只会把延迟拖到它的水平。一条都没过闸才等到硬上限 deadline，
    到点放弃，并把「超时」/「没等它」记进实况：搜不到时这两者要能分开，否则又在猜。
    """
    plan = _allocate(backends, queries)
    if not plan:
        return [], [], []
    if deadline is not None and time.monotonic() >= deadline:
        return [], [], []          # 时间已经用尽：这一轮根本不该再发请求
    tasks = {
        i: asyncio.create_task(_fetch_one(client, b, q, PER_REQUEST, exclude, budget,
                                          require_adjacent=require_adjacent))
        for i, (b, q) in enumerate(plan)
    }
    if soft is not None and deadline is not None:
        soft = min(soft, deadline)
    pending: set = set(tasks.values())
    done: set = set()
    abandoned = "超时"
    if soft is not None:
        more, pending = await asyncio.wait(pending, timeout=max(soft - time.monotonic(), 0.2))
        done |= more
    if pending and _passed(done):
        abandoned = "没等它"
        for task in pending:
            task.cancel()
    elif pending:
        more, pending = await asyncio.wait(
            pending, timeout=None if deadline is None else max(deadline - time.monotonic(), 0.2))
        done |= more
        for task in pending:
            task.cancel()
    items: list[dict] = []
    failed: list[str] = []
    stats: list[dict] = []
    for i in sorted(tasks):                      # 顺序固定，实况与结果都可复现
        backend, query = plan[i]
        task = tasks[i]
        if task in pending or task.cancelled():
            failed.append(f"{backend.name} {abandoned}")
            stats.append(_stat(backend, query, 0, 0, abandoned))
        elif task.exception() is not None:
            failed.append(f"{backend.name} {type(task.exception()).__name__}")
            stats.append(_stat(backend, query, 0, 0, type(task.exception()).__name__))
        else:
            got, err, stat = task.result()
            items.extend(got)
            stats.append(stat)
            if err:
                failed.append(err)
    return items, failed, stats


def _starved(backends, stats) -> list:
    """这一轮「引擎自己没给东西」的后端——回退该重跑谁，由它决定。

    解析为 0 且不是预算用尽：预算用尽是编排层自己的事，重跑也还是没预算。
    """
    out = []
    for backend in backends:
        rows = [a for a in stats if a["provider"] == backend.name]
        if rows and all(a["parsed"] == 0 and a.get("error") != "预算用尽" for a in rows):
            out.append(backend)
    return out


def hint_for(attempts: list[dict], queries: list[str]) -> str:
    """空手时把实况讲清楚：谁说的、为什么、下一步。

    这段文字会被模型转述给用户（实测它把这里的话说成「搜索引擎自己都标了」），
    所以只写事实：哪几家引擎、取回几条、是引擎没给还是闸门拦的、该换什么检索式。
    """
    parsed = sum(int(a.get("parsed") or 0) for a in attempts)
    engines = sorted({str(a.get("provider") or "") for a in attempts if a.get("provider")})
    said = f"{len(engines)} 家引擎（{'、'.join(engines) or '无'}）一共取回 {parsed} 条，没有一条过相关性闸门。"
    if parsed == 0:
        why = "是引擎这一轮没给东西（冷门词、超时或临时抽风）：换个说法，或过一会儿再搜。"
    elif not any(has_gate_signal(q) for q in queries):
        why = ("检索式里没有可判定的实体词——项目/开源/平台这类泛词不算实体，"
               "补一个专名、引号短语或英文说法再搜。")
    else:
        why = ("取回的内容没有一条命中检索式里的实体词或中文词块；如果其中有你猜的音译外文名或别称，"
               "换成中文说法或英文通用词再搜一次。")
    return (said + why + "下一步建议：向用户索要官网 / GitHub / Product Hunt / X 的链接；"
            "拿到链接后用 web_fetch 打开，官网可先试 /sitemap.xml、/blog、/changelog、/docs、/release-notes。")


def entity_terms_of(query: str) -> list[str]:
    """打分用的实体词：有实体词就用它（品牌名基本都在这），纯中文查询退回二元组。"""
    return entity_terms(query) or sorted(cjk_bigrams(query))


async def search(queries, *, fallback_raw: str = "", exclude=(), limit: int = 8,
                 per_domain: int = 3) -> dict:
    """检索式进，排好序的结果出。任何一级空手而归都不会让整次调用变成「没搜到」就结束。"""
    raw = fallback_raw or (queries[0] if queries else "")
    given_count = len([q for q in (queries or []) if str(q).strip()])
    candidates = merge_queries(queries, raw)
    # merge_queries 先放调用方给的、再放机械改写补的：按这个分界把两类分开
    probes = candidates[:given_count] or list(candidates)
    variants = candidates[len(probes):]
    exclude = [str(t).strip() for t in (exclude or []) if str(t).strip()]
    if not candidates:
        return {"success": False, "error": "没有可用的检索式", "results": [], "count": 0}

    budget = _Budget(MAX_REQUESTS)
    deadline = time.monotonic() + DEADLINE
    collected: list[dict] = []
    failed: list[str] = []
    attempts: list[dict] = []
    async with httpx.AsyncClient(follow_redirects=True, timeout=TIMEOUT) as client:
        collected, failed, stats = await _fan_out(
            client, list(BACKENDS), probes, exclude, budget,
            require_adjacent=not given_count, deadline=deadline,
            soft=time.monotonic() + SOFT_DEADLINE)
        attempts.extend(stats)
        if not collected:
            # 空手分两种：引擎没给东西 -> 那几家重跑；引擎给了但全被拦 -> 换检索式
            starved = _starved(list(BACKENDS), stats)
            if starved:
                retry_backends, retry_queries = starved, probes
                adjacent = not given_count
            else:
                retry_backends, retry_queries = list(BACKENDS), variants
                adjacent = True
            if retry_queries and retry_backends and budget.left > 0 and time.monotonic() < deadline:
                more, errors, stats = await _fan_out(
                    client, retry_backends, retry_queries, exclude, budget,
                    require_adjacent=adjacent, deadline=deadline,
                    soft=time.monotonic() + SOFT_DEADLINE)
                failed.extend(errors)
                attempts.extend(stats)
                collected = more
    logger.info("搜索实况 %s", [(a["provider"], a["query"][:24], a["parsed"], a["kept"]) for a in attempts])

    kept = [i for i in collected if not _excluded(i, exclude)]
    ranked = rank(kept, entity_terms=entity_terms_of(probes[0]),
                  per_domain=per_domain, limit=limit)
    out = {
        "success": True,
        "queries": candidates,
        "provider_used": sorted({e for i in ranked for e in (i.get("engines") or []) if e}),
        "count": len(ranked),
        "results": ranked,
        "attempts": attempts,
    }
    if failed:
        out["failed"] = failed
    if not ranked:
        out["hint"] = hint_for(attempts, probes)
    return out
