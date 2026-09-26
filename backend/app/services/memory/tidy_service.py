"""记忆整理 — 定期把该走的清掉、把重复的并起来

为什么放在后台而不是读取路径：读取路径只该算权重、决定注不注入；真删除要落盘，
混进一次普通对话就是写放大，还会拖慢回复。

两条规则（见 docs/memory_system/design/focus_and_memory_reach.md 第七节）：

  - 设定权值最低（≤ DELETE_MAX_WEIGHT）且有效权重已跌破阈值的：物理删除，腾出空间；
  - 自动提取的待归档条目（pending_archive）：同类型的同名只留一条，独特的转正为 active。

其余记忆一律不动——权值高的即使淡出也只是不再注入，记录留着，显式召回仍取得到。
"""
import asyncio
import logging

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.memory import RoughMemory
from app.utils.pure.memory_weight import DELETE_MAX_WEIGHT, disposition_at
from app.utils.pure.timeutil import utc_now

logger = logging.getLogger(__name__)

TIDY_INTERVAL_SECONDS = 24 * 3600


async def tidy_agent_memories(db: AsyncSession, agent_id: int) -> dict:
    """整理一个 AI 的记忆，返回各档计数。"""
    call_count = (await db.execute(
        text("SELECT llm_call_count FROM agents WHERE id = :a"), {"a": agent_id}
    )).scalar() or 0

    rows = (await db.execute(select(RoughMemory).where(
        RoughMemory.owner_type == "ai", RoughMemory.owner_id == agent_id
    ))).scalars().all()

    now = utc_now()
    deleted = merged = promoted = 0
    seen: set[str] = set()

    for row in rows:
        weight = row.value_score or 0
        if weight <= DELETE_MAX_WEIGHT and disposition_at(
                weight, row.last_touched_at, row.last_touched_call,
                now, call_count) == "delete":
            await db.delete(row)
            deleted += 1
            continue

        if row.status == "pending_archive":
            key = f"{row.mem_type}:{(row.title or '')[:20]}"
            if key in seen:
                await db.delete(row)
                merged += 1
                continue
            seen.add(key)
            row.status = "active"
            promoted += 1

    await db.flush()
    return {"scanned": len(rows), "deleted": deleted, "merged": merged, "promoted": promoted}


async def tidy_all_agents(db: AsyncSession) -> dict:
    """对所有不在离线状态的 AI 跑一遍整理；单个失败不影响其余。"""
    agent_ids = [r[0] for r in (await db.execute(
        text("SELECT id FROM agents WHERE state != 'inactive'"))).all()]

    total = {"agents": len(agent_ids), "deleted": 0, "merged": 0, "promoted": 0}
    for agent_id in agent_ids:
        try:
            out = await tidy_agent_memories(db, agent_id)
        except Exception as e:
            logger.warning(f"AI({agent_id}) 记忆整理失败: {e}")
            await db.rollback()
            continue
        await db.commit()
        for key in ("deleted", "merged", "promoted"):
            total[key] += out[key]
    return total


async def memory_tidy_worker() -> None:
    """后台 worker：每天整理一次。

    首次不立刻跑：重启很频繁，一启动就删数据会把"重启"变成有副作用的事。
    """
    from app.database import async_session

    logger.info("🧹 记忆整理 worker 已启动（间隔=%ds）", TIDY_INTERVAL_SECONDS)
    while True:
        await asyncio.sleep(TIDY_INTERVAL_SECONDS)
        try:
            async with async_session() as db:
                out = await tidy_all_agents(db)
            logger.info(f"🧹 记忆整理完成: {out}")
        except Exception as e:
            logger.warning(f"记忆整理失败（下轮重试）: {e}")
