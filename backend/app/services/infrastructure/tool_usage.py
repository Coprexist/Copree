"""
工具调用统计的唯一写入入口。

为什么单一入口：调用点会越来越多（群聊 dispatch、决策代理代发、PTC 回桥），
每个地方各写一遍 UPSERT 必然有人漏字段、漏维度。这里收口成两个函数——
取值（当前跑在哪个帧、什么状态）与写（日聚合累加）各一处。

失败处理：统计是旁路，绝不能影响工具本身的结果。用 SAVEPOINT 隔离——
直接吞掉异常会留下一个「当前事务已中止」的 PostgreSQL 事务，接下来所有查询全挂。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# 没有帧 / 读不到状态时的哨兵值（见 ToolUsageDaily 的列注释）
UNKNOWN = ""


async def current_tool_context(db: AsyncSession, agent_id: int, context: dict | None = None) -> tuple[str, str]:
    """这次调用发生在哪个帧、什么状态 → (frame_type, agent_state)。

    两个都按**调用这一刻**现读：一轮里可能 push_state 换帧，按轮开始的值记会把
    push_state 自己记到上一帧去。
    """
    agent = (context or {}).get("_agent")
    state = getattr(agent, "state", None)
    frame_type = UNKNOWN
    try:
        from app.services.agent.state_stack_service import get_current_frame
        frame = await get_current_frame(db, agent_id)
        if frame:
            frame_type = str(frame.get("type") or UNKNOWN)
    except Exception as e:  # noqa: BLE001 —— 统计维度缺失不该冒泡
        logger.debug("tool_usage: 读当前帧失败: %s", e)
    if not state:
        try:
            from app.models.agent import Agent
            row = await db.get(Agent, agent_id)
            state = getattr(row, "state", None) or UNKNOWN
        except Exception as e:  # noqa: BLE001
            logger.debug("tool_usage: 读 agent 状态失败: %s", e)
    return frame_type, str(state or UNKNOWN)


async def record_tool_call(
    db: AsyncSession,
    agent_id: int,
    tool_name: str,
    *,
    success: bool,
    latency_seconds: float = 0.0,
    context: dict | None = None,
) -> None:
    """把一次工具调用累加到当天那一行（维度：工具 × 帧类型 × 在线状态）"""
    try:
        frame_type, agent_state = await current_tool_context(db, agent_id, context)
        params = {
            "stat_date": datetime.now(timezone.utc).date(),
            "tool_name": str(tool_name or "")[:80],
            "frame_type": frame_type[:40],
            "agent_state": agent_state[:20],
            "calls": 1,
            "errors": 0 if success else 1,
            "latency_ms_sum": max(0, int((latency_seconds or 0.0) * 1000)),
        }
        # SAVEPOINT：统计写失败只回滚这一小段，不把调用方的事务一起带走
        async with db.begin_nested():
            await db.execute(text("""
                INSERT INTO tool_usage_daily (
                    stat_date, tool_name, frame_type, agent_state,
                    calls, errors, latency_ms_sum, updated_at
                ) VALUES (
                    :stat_date, :tool_name, :frame_type, :agent_state,
                    :calls, :errors, :latency_ms_sum, CURRENT_TIMESTAMP
                )
                ON CONFLICT (stat_date, tool_name, frame_type, agent_state) DO UPDATE SET
                    calls = tool_usage_daily.calls + EXCLUDED.calls,
                    errors = tool_usage_daily.errors + EXCLUDED.errors,
                    latency_ms_sum = tool_usage_daily.latency_ms_sum + EXCLUDED.latency_ms_sum,
                    updated_at = CURRENT_TIMESTAMP
            """), params)
    except Exception as e:  # noqa: BLE001 —— 统计旁路，任何异常都不影响工具结果
        logger.warning("工具调用统计写入失败（非致命）: %s", e)


async def tool_usage_summary(db: AsyncSession, *, days: int = 30, limit: int = 200) -> list[dict]:
    """最近 N 天「工具 × 帧类型 × 状态」的调用汇总（管理台/分析用，按调用次数降序）

    时间窗在 Python 里算好传参，不写 `current_date - interval 'N days'`：
    那是 PG 语法，安装包走 SQLite 会直接报错。日期加减两边都认参数。
    """
    from datetime import date, timedelta
    cutoff = date.today() - timedelta(days=int(days))
    rows = await db.execute(text("""
        SELECT tool_name, frame_type, agent_state,
               SUM(calls) AS calls, SUM(errors) AS errors,
               SUM(latency_ms_sum) AS latency_ms_sum
        FROM tool_usage_daily
        WHERE stat_date >= :cutoff
        GROUP BY tool_name, frame_type, agent_state
        ORDER BY calls DESC
        LIMIT :limit
    """), {"cutoff": cutoff, "limit": int(limit)})
    return [dict(r._mapping) for r in rows]
