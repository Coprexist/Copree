"""
世界懒加载调度器 — 周期扫描休眠/活跃世界

- 活跃世界：超时（默认 10 分钟）无活动 → 休眠（零占用）
- 休眠世界：近期有活动（对话/预览）→ 唤醒 + 离线时间补偿
- 活动信号：last_active_at（对话、预览、手动 wake 都会更新）

⚠️ 手动模式：AUTO_MANAGE=False 时调度器不做任何
状态切换——世界状态只由手动 wake/sleep 端点控制（唤醒后保持活跃，
不再 10 分钟自动转回休眠）。改回 True 恢复自动休眠/唤醒。

间隔 60s 扫描一次，开销极小（两条轻查询 / 手动模式 no-op）。
"""
import asyncio
import logging
from datetime import timedelta

from sqlalchemy import select

from app.database import async_session
from app.services.world.world_service import _now, apply_time_compensation

from app.repositories.world_repo import SQLAlchemyWorldRepository
from sqlalchemy.ext.asyncio import AsyncSession
logger = logging.getLogger(__name__)

def _ensure_repo(db_or_repo):
    """兼容旧调用：传入 AsyncSession 时包装为 SQLAlchemyWorldRepository。"""
    if isinstance(db_or_repo, AsyncSession):
        return SQLAlchemyWorldRepository(db_or_repo)
    return db_or_repo


SCAN_INTERVAL = 60            # 扫描间隔（秒）
INACTIVE_TIMEOUT_MIN = 10     # 活跃超时（分钟）→ 休眠
AUTO_MANAGE = False           # 手动模式：世界状态只由手动 wake/sleep 控制


async def sweep_worlds(db):
    """一轮扫描：休眠超时的活跃世界 + 唤醒有活动的休眠世界"""
    db = _ensure_repo(db)
    if not AUTO_MANAGE:
        # 手动模式：状态切换全部交给手动 wake/sleep 端点
        return
    from app.models.world import World

    now = _now()
    cutoff = now - timedelta(minutes=INACTIVE_TIMEOUT_MIN)

    # 1. 活跃 → 休眠（超时未活动）
    slept = 0
    res = await db.execute(
        select(World).where(
            World.status == "active",
            World.last_active_at < cutoff,
        )
    )
    for w in res.scalars():
        w.status = "sleeping"
        slept += 1

    # 2. 休眠 → 活跃（近期有活动）+ 离线时间补偿
    woke = 0
    res2 = await db.execute(
        select(World).where(
            World.status == "sleeping",
            World.last_active_at >= cutoff,
        )
    )
    for w in res2.scalars():
        apply_time_compensation(w, now)
        woke += 1

    if slept or woke:
        await db.commit()
        logger.info(f"🌐 世界调度: {woke} 个唤醒(含时间补偿), {slept} 个转入休眠")


async def world_scheduler():
    """懒加载调度 worker（main.py lifespan 启动）"""
    logger.info("🌐 世界懒加载调度器已启动")
    while True:
        await asyncio.sleep(SCAN_INTERVAL)
        try:
            async with async_session() as db:
                await sweep_worlds(db)
        except Exception as e:
            logger.warning(f"🌐 世界调度异常: {e}")
