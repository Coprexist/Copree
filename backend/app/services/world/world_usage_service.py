"""
群视界 LLM 用量的聚合查询 — 世界设计页、世界入口受控 API、控制台统计表三处共用。

`world_llm_usage` 是全仓唯一记到 world_id 的用量表（`usage_daily` 只记到 agent/user，
世界 AI 落的是 agent_id=0），所以「按世界」的统计只能从这里出。
命中率一律走 cache_stats：同一份账在三处各自四舍五入，面板之间就会对不上。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.models.world import World, WorldLLMUsage
from app.utils.pure.cache_stats import cache_hit_rate_pct


async def world_usage_totals(db: AsyncSession, world_id: int) -> dict:
    """单个世界的全时段用量（不含分轮明细：那是设计页自己的画法）"""
    row = (await db.execute(
        select(
            func.count(WorldLLMUsage.id),
            func.coalesce(func.sum(WorldLLMUsage.prompt_tokens), 0),
            func.coalesce(func.sum(WorldLLMUsage.completion_tokens), 0),
            func.coalesce(func.sum(WorldLLMUsage.cached_tokens), 0),
        ).where(WorldLLMUsage.world_id == world_id)
    )).one()
    calls, prompt, completion, cached = row
    return {
        "total_calls": calls,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "cached_tokens": cached,
        "cache_hit_rate_pct": cache_hit_rate_pct(prompt, cached),
    }


async def worlds_usage_overview(
    db: AsyncSession,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
) -> list[dict]:
    """按世界聚合的用量行（区间内没有调用的世界不占行），按总 token 降序"""
    stmt = (
        select(
            World.id,
            World.name,
            World.owner_id,
            User.username,
            func.count(WorldLLMUsage.id),
            func.coalesce(func.sum(WorldLLMUsage.prompt_tokens), 0),
            func.coalesce(func.sum(WorldLLMUsage.completion_tokens), 0),
            func.coalesce(func.sum(WorldLLMUsage.reasoning_tokens), 0),
            func.coalesce(func.sum(WorldLLMUsage.cached_tokens), 0),
            func.max(WorldLLMUsage.created_at),
        )
        .select_from(WorldLLMUsage)
        .join(World, World.id == WorldLLMUsage.world_id)
        # 世界主人被删时世界会跟着删（外键 CASCADE），这里的 LEFT JOIN 只是不让账号缺失整行消失
        .outerjoin(User, User.id == World.owner_id)
        .group_by(World.id, World.name, World.owner_id, User.username)
    )
    if start_date:
        stmt = stmt.where(WorldLLMUsage.created_at >= start_date)
    if end_date:
        stmt = stmt.where(WorldLLMUsage.created_at <= end_date)

    rows = [
        {
            "world_id": wid,
            "world_name": name,
            "owner_id": owner_id,
            "owner_name": username or "",
            "total_calls": calls,
            "total_tokens": prompt + completion + reasoning,
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "reasoning_tokens": reasoning,
            "cached_tokens": cached,
            "cache_hit_rate_pct": cache_hit_rate_pct(prompt, cached),
            "last_at": last_at.isoformat() if last_at else None,
        }
        for wid, name, owner_id, username, calls, prompt, completion, reasoning, cached, last_at
        in (await db.execute(stmt)).all()
    ]
    rows.sort(key=lambda r: r["total_tokens"], reverse=True)
    return rows


async def worlds_usage_daily(
    db: AsyncSession,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
) -> list[dict]:
    """群视界按天的用量曲线

    形状与 usage_daily 的每日曲线逐字段一致（date/total/prompt/completion/reasoning/
    cached/request_count/hit），前端两边的画法就能共用一套。
    """
    day = func.date(WorldLLMUsage.created_at)
    stmt = (
        select(
            day,
            func.count(WorldLLMUsage.id),
            func.coalesce(func.sum(WorldLLMUsage.prompt_tokens), 0),
            func.coalesce(func.sum(WorldLLMUsage.completion_tokens), 0),
            func.coalesce(func.sum(WorldLLMUsage.reasoning_tokens), 0),
            func.coalesce(func.sum(WorldLLMUsage.cached_tokens), 0),
        )
        .group_by(day)
        .order_by(day)
    )
    if start_date:
        stmt = stmt.where(WorldLLMUsage.created_at >= start_date)
    if end_date:
        stmt = stmt.where(WorldLLMUsage.created_at <= end_date)

    return [
        {
            "date": str(stat_date),
            "total_tokens": prompt + completion + reasoning,
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "reasoning_tokens": reasoning,
            "cached_tokens": cached,
            "request_count": calls,
            "cache_hit_rate_pct": cache_hit_rate_pct(prompt, cached),
        }
        for stat_date, calls, prompt, completion, reasoning, cached
        in (await db.execute(stmt)).all()
    ]
