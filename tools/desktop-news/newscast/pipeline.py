"""主流水线：抓取 → 时间窗口过滤 → 去重聚类 → 概括 → 落盘。

一次 run_once 的产出既是界面数据源，也是「上次运行到这次运行」这条时间线的推进点。
"""

from __future__ import annotations

import datetime as _dt
import logging
import time
from typing import Callable

from . import classify, searchapi, sources, textutil
from .ai import AIClient
from .config import category_map, resolve_api_key
from .models import CategoryBlock, NewsCard, RunResult, SourceStatus
from .store import Store

ProgressFn = Callable[[str], None]


def _noop(_msg: str) -> None:
    pass


def resolve_window(cfg: dict, last_run: _dt.datetime | None, now: _dt.datetime) -> tuple[_dt.datetime, _dt.datetime]:
    """确定本次要覆盖的时间区间：起点取「上次运行时间」。

    没有历史（首次运行）或上次运行太久（假期回来），就回退到默认时长并加上限，
    否则一次抓回几百条，既费 token 又没人看得完。
    """
    app = cfg["app"]
    default_hours = float(app.get("window_hours_default", 24))
    max_hours = float(app.get("max_window_hours", 96))
    if last_run is None or last_run > now:  # 无历史或系统时间被改过（一体机常见）
        return now - _dt.timedelta(hours=default_hours), now
    if (now - last_run) > _dt.timedelta(hours=max_hours):
        return now - _dt.timedelta(hours=max_hours), now
    return last_run, now


def _in_window(item, start: _dt.datetime, end: _dt.datetime, seen: dict,
               late_grace_hours: float = 12.0) -> bool:
    """判断条目是否属于本次要处理的区间。

    没有时间戳的条目（部分源的 RSS 不给 pubDate）只要没见过就收——漏掉新闻比多显示一条更糟。
    略早于起点但没见过的条目也收（源站时区标注不准、发布延迟是常态），但只放宽 12 小时。
    """
    if item.published is None:
        return item.fingerprint not in seen
    if item.published > end + _dt.timedelta(minutes=5):
        return False  # 源站时间标到未来，丢掉
    if item.published >= start:
        return True
    if item.fingerprint in seen:
        return False
    return item.published >= start - _dt.timedelta(hours=late_grace_hours)


def _cached_card(cluster: classify.Cluster, cache: dict) -> NewsCard | None:
    """命中 AI 缓存就直接复用，白天多次刷新不再重复花钱。"""
    for item in cluster.items:
        entry = cache.get(item.fingerprint)
        if isinstance(entry, dict) and entry.get("title") and entry.get("summary"):
            return NewsCard(
                title=entry["title"],
                summary=entry["summary"],
                angle=entry.get("angle", ""),
                category=entry.get("category") or cluster.category,
                sources=list(cluster.sources),
                urls=list(cluster.urls),
                published=cluster.published,
                item_count=cluster.item_count,
                score=cluster.score + 1.0,
                from_ai=True,
            )
    return None


def _write_cache(cache: dict, card: NewsCard, members: list[classify.Cluster]) -> None:
    """按键条目指纹记录 AI 结果：下次同一事件再有报道进来也能直接复用概括。"""
    stamp = textutil.iso(textutil.now_utc())
    payload = {"title": card.title, "summary": card.summary, "angle": card.angle,
               "category": card.category, "at": stamp}
    for cluster in members:
        for item in cluster.items:
            cache[item.fingerprint] = payload


def run_once(cfg: dict, store: Store, logger: logging.Logger,
             use_ai: bool = True, use_search: bool | None = None,
             advance_state: bool = True, progress: ProgressFn | None = None) -> RunResult:
    notify = progress or _noop
    started = time.time()
    now = textutil.now_utc()
    state = store.load_state()
    seen: dict = state.get("seen") or {}
    last_run = textutil.parse_time(state.get("last_run"))
    start, end = resolve_window(cfg, last_run, now)
    logger.info("时间窗口：%s → %s", textutil.iso(start), textutil.iso(end))
    notify(f"正在抓取新闻源（{textutil.format_local(start)} 起）")

    all_items, source_status = sources.fetch_all(cfg, logger)

    search_cfg = cfg.get("search", {})
    want_search = bool(search_cfg.get("enabled")) if use_search is None else bool(use_search)
    if want_search:
        key = resolve_api_key(search_cfg)
        if key:
            notify("正在调用搜索接口补充")
            found = searchapi.search_all(cfg, search_cfg, key, logger)
            all_items.extend(found)
            source_status.append(SourceStatus(name=f"搜索（{search_cfg.get('provider', '')}）",
                                              ok=bool(found), items=len(found),
                                              error="" if found else "未返回结果"))
        else:
            source_status.append(SourceStatus(name="搜索接口", ok=False, error="未配置 API key"))
            logger.warning("已开启搜索但未配置 API key，跳过")

    fetched = len(all_items)
    kept = [i for i in all_items if _in_window(i, start, end, seen)]
    logger.info("抓取 %d 条，落在时间窗口内 %d 条", fetched, len(kept))

    if not kept:
        note = "本次时间窗口内没有新条目"
        if advance_state:
            # 本来就没有新内容也推进时间线：否则断网一次就会一直卡在同一个起点上
            state["last_run"] = textutil.iso(end)
            state["runs"] = int(state.get("runs") or 0) + 1
            store.save_state(state)
        # 教室里最忌屏幕空掉：没有新内容就继续显示上次结果，只更新状态栏
        previous = store.load_latest()
        if previous is not None:
            previous.note = note + "，继续显示上次内容"
            previous.source_status = source_status
            previous.fetched = fetched
            previous.kept = 0
            previous.duration = time.time() - started
            return previous
        return RunResult(generated_at=now, window_start=start, window_end=end,
                         source_status=source_status, fetched=fetched, kept=0,
                         duration=time.time() - started, note=note)

    notify(f"正在整理 {len(kept)} 条新闻")
    categories = cfg.get("categories", [])
    clusters = classify.cluster_items(kept, categories, logger,
                                      threshold=float(cfg["app"].get("cluster_threshold", 0.42)))

    cache = store.load_cache()
    cards: list[NewsCard] = []
    pending: list[classify.Cluster] = []
    for cluster in clusters:
        cached = _cached_card(cluster, cache)
        if cached is not None:
            cards.append(cached)
        else:
            pending.append(cluster)
    if len(cards):
        logger.info("AI 缓存命中 %d 个候选事件", len(cards))

    ai_used = bool(cards)          # 缓存命中也算「有 AI 质量的概括」
    ai_error = ""
    covered: set[int] = set()
    fresh_cache_entries: list[tuple[NewsCard, list[classify.Cluster]]] = []

    client = AIClient(cfg, logger)
    if not use_ai:
        ai_error = "本次运行未启用 AI"
    elif pending:
        if client.available:
            budget = max(5, int(cfg["ai"].get("max_items_per_call", 22))) * max(1, int(cfg["ai"].get("max_batches", 3)))
            notify(f"AI 正在概括 {min(len(pending), budget)} 个候选事件")
            pairs, used, ai_error = client.summarize(pending, (start, end), categories, cache)
            for card, members in pairs:
                cards.append(card)
                covered.update(m.cid for m in members)
                fresh_cache_entries.append((card, members))
            ai_used = ai_used or used
        else:
            ai_error = client.unavailable_reason()
            logger.info("跳过 AI：%s", ai_error)

    # AI 没覆盖到的候选走规则卡片，保证不漏新闻
    for cluster in clusters:
        if cluster.cid in covered:
            continue
        cards.append(classify.heuristic_card(cluster))

    result = _assemble(cfg, cards, source_status, fetched, len(kept), now, start, end,
                       ai_used, ai_error, time.time() - started)

    # 只有真产出卡片时才推进时间线并记账：AI 或网络故障时下次仍处理同一段，能自动补上
    if advance_state and result.has_content:
        for card, members in fresh_cache_entries:
            _write_cache(cache, card, members)
        if fresh_cache_entries:
            store.save_cache(cache, keep_days=int(cfg["ai"].get("cache_days", 10)))
        for item in kept:
            seen[item.fingerprint] = textutil.iso(now)
        state["seen"] = seen
        state["last_run"] = textutil.iso(end)
        state["runs"] = int(state.get("runs") or 0) + 1
        state["last_error"] = ""
        store.prune_seen(state)
        store.save_state(state)
        store.save_latest(result)
    elif not result.has_content:
        state["last_error"] = "本次未产出任何卡片"
        store.save_state(state)
    return result


def _assemble(cfg: dict, cards: list[NewsCard], source_status, fetched: int, kept: int,
              now: _dt.datetime, start: _dt.datetime, end: _dt.datetime,
              ai_used: bool, ai_error: str, duration: float) -> RunResult:
    """按分类归并、排序、截断，产出最终上屏结构。"""
    cats = cfg.get("categories", [])
    mapping = category_map(cfg)
    fallback = cats[0]["key"] if cats else "politics"

    # 同一事件可能既有 AI 卡片又有规则卡片，按标题相似去掉信息量低的那张
    unique: list[NewsCard] = []
    for card in sorted(cards, key=lambda c: c.score, reverse=True):
        if any(textutil.is_same_event(card.title, u.title, 0.86) and card.category == u.category
               for u in unique):
            continue
        unique.append(card)

    blocks: list[CategoryBlock] = []
    for cat in cats:
        key = cat["key"]
        limit = int(cat.get("max") or 4)
        picked = [c for c in unique if c.category == key][:limit]
        blocks.append(CategoryBlock(key=key, label=cat.get("label", key),
                                    accent=cat.get("accent", "#8fa3b8"), cards=picked))

    # 分类没兜住的卡片塞进兜底分类，避免辛苦抓来的新闻被丢掉
    leftovers = [c for c in unique if c.category not in mapping]
    if leftovers and blocks:
        target = next((b for b in blocks if b.key == fallback), blocks[0])
        target.cards.extend(leftovers[:2])

    return RunResult(generated_at=now, window_start=start, window_end=end, blocks=blocks,
                     source_status=source_status, fetched=fetched, kept=kept,
                     ai_used=ai_used, ai_error=ai_error,
                     ai_cards=sum(1 for c in unique if c.from_ai),
                     duration=duration)
