"""
应用生命周期引导 — 从 main.py 拆出的启动/关闭流程。

原则：
- 启动按职责拆分为多个 _startup_xxx()，关闭为 _shutdown_xxx()，lifespan 统一编排
- 后台任务统一走 spawn_task：带异常日志与可选的退避自动重启（循环型任务挂掉自动拉起）
- 定时任务用 sleep_until 对齐到固定时刻，而不是固定 24h sleep
- 核心依赖（数据库）快速失败，非核心步骤降级 warning 不阻塞启动
"""
import asyncio
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from fastapi import FastAPI
from sqlalchemy import text

from app.config import settings
from app.database import async_session, engine
from app.services.infrastructure.maintenance import maintenance

logger = logging.getLogger("app.bootstrap")


# ══════════════════════════════════════════════════════════════
# 后台任务管理（异常日志 + 自动重启）
# ══════════════════════════════════════════════════════════════

_BACKGROUND_TASKS: dict[str, asyncio.Task] = {}
_DELAYED_RESTART_TASKS: set[asyncio.Task] = set()

# 重启计数重置阈值（秒）：任务稳定运行超过此时间后，重启计数归零
_RESTART_COUNT_RESET_SECONDS = 600


def spawn_task(factory, name: str, *, restart: bool = False, max_restarts: int = 10) -> asyncio.Task:
    """创建后台任务。

    factory 是协程工厂（每次启动/重启时调用，避免协程对象不可复用），例如传
    ai_response_worker 而非 ai_response_worker()。

    restart=True 时，任务异常退出按指数退避自动重启（1,2,4...封顶 60s），
    连续超过 max_restarts 次放弃并记 CRITICAL，避免死循环狂重启。
    正常结束（return）不重启。
    任务稳定运行超过 _RESTART_COUNT_RESET_SECONDS 后重启计数归零。
    """
    state = {"restarts": 0, "task": None, "last_restart_time": 0.0}

    def _done(t: asyncio.Task) -> None:
        if t.cancelled():
            return
        exc = t.exception()
        if exc is None:
            return

        now = time.monotonic()
        # 稳定运行超过阈值，重置重启计数（偶发故障恢复后允许重新重启）
        if state["last_restart_time"] > 0 and now - state["last_restart_time"] > _RESTART_COUNT_RESET_SECONDS:
            state["restarts"] = 0

        if restart and state["restarts"] < max_restarts:
            state["restarts"] += 1
            state["last_restart_time"] = now
            delay = min(60, 2 ** state["restarts"])
            logger.error(
                f"[RESTART] 后台任务 {name} 异常退出，{delay}s 后自动重启"
                f"（第 {state['restarts']}/{max_restarts} 次）: {exc!r}",
                exc_info=(type(exc), exc, exc.__traceback__),
            )

            async def _restart_later():
                await asyncio.sleep(delay)
                _launch()

            delay_task = asyncio.create_task(_restart_later())
            _DELAYED_RESTART_TASKS.add(delay_task)
            delay_task.add_done_callback(_DELAYED_RESTART_TASKS.discard)
        else:
            logger.critical(
                f"[CRITICAL] 后台任务 {name} 异常退出且放弃重启: {exc!r}",
                exc_info=(type(exc), exc, exc.__traceback__),
            )

    def _launch() -> asyncio.Task:
        task = asyncio.create_task(factory())
        state["task"] = task
        task.add_done_callback(_done)
        _BACKGROUND_TASKS[name] = task
        return task

    return _launch()


async def cancel_all_tasks() -> None:
    """关闭时统一取消所有后台任务（含延迟重启任务）"""
    # 先取消延迟重启任务（防止应用关闭后任务复活）
    for t in list(_DELAYED_RESTART_TASKS):
        t.cancel()
    for t in list(_DELAYED_RESTART_TASKS):
        try:
            await t
        except asyncio.CancelledError:
            pass
    _DELAYED_RESTART_TASKS.clear()

    # 再取消主任务
    tasks = list(_BACKGROUND_TASKS.values())
    for t in tasks:
        t.cancel()
    for t in tasks:
        try:
            await t
        except asyncio.CancelledError:
            pass
    _BACKGROUND_TASKS.clear()


# ══════════════════════════════════════════════════════════════
# 通用工具
# ══════════════════════════════════════════════════════════════

def sleep_until(hour: int, minute: int = 0) -> float:
    """计算到下一个 hour:minute 的秒数（今天已过则顺延到明天）"""
    now = datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


async def wait_for_db(timeout: float = 30.0) -> None:
    """等待数据库就绪：0.5s 轮询，超时抛 RuntimeError 快速失败（核心依赖不降级运行）"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    warned = False
    while True:
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return
        except Exception:
            if not warned:
                logger.warning(f"[WAIT] 等待数据库就绪（最多 {timeout:.0f}s，超时将中止启动）...")
                warned = True
            if loop.time() >= deadline:
                raise RuntimeError(f"数据库在 {timeout:.0f}s 内未就绪，启动中止")
            await asyncio.sleep(0.5)


# ══════════════════════════════════════════════════════════════
# 启动步骤
# ══════════════════════════════════════════════════════════════

async def _startup_db() -> None:
    """数据库就绪 + 迁移 + DB 配置覆盖 + 在线用户活跃时间重置"""
    await wait_for_db()
    logger.info("[OK] 数据库连接正常")

    from app.database import engine
    from app.db_migrate import prepare_database
    await prepare_database(engine)
    logger.info("[OK] 数据库初始化完成")

    # 加载 DB 覆盖配置（管理员前端图形化修改的配置组，覆盖 env）
    try:
        from app.services.infrastructure.app_config_service import load_all_configs
        async with async_session() as cfg_db:
            await load_all_configs(cfg_db)
    except Exception as e:
        logger.warning(f"[WARN] 加载 DB 配置覆盖失败（使用 env 配置）: {e}")

    # 启动时将 last_active_at=NULL 标记为当前时间（服务器重启前在线的用户）
    from sqlalchemy import update as sa_update, func
    from app.models.user import User as UserModel
    try:
        async with async_session() as startup_db:
            await startup_db.execute(
                sa_update(UserModel).where(UserModel.last_active_at.is_(None)).values(last_active_at=func.now())
            )
            await startup_db.commit()
        logger.info("[OK] 已重置在线用户的上次活跃时间")
    except Exception as e:
        logger.warning(f"[WARN] 重置在线用户活跃时间失败: {e}", exc_info=True)


async def _startup_plugins() -> None:
    """统一插件：磁盘扫描同步 + 技能插件注册 + 平台能力版本化"""
    try:
        from app.services.plugin.catalog import sync_plugins_to_db
        from app.services.plugin.skill_bridge import apply_skill_plugins
        async with async_session() as plugin_db:
            changed = await sync_plugins_to_db(plugin_db)
            await apply_skill_plugins(plugin_db)
        logger.info(f"[OK] 插件目录同步完成（{changed} 项变更）")
    except Exception as e:
        logger.warning(f"[WARN] 插件目录同步失败（不影响启动）: {e}", exc_info=True)

    # 平台能力版本化（skills/tools 懒加载）：对比内置工具定义、变更则写新版本。
    # 纯 DB 记账，不挡启动关键路径（2026-09-25：实测这一步要 ~1.5s）
    spawn_task(_ensure_platform_version_once, "_ensure_platform_version_once")


async def _ensure_platform_version_once() -> None:
    try:
        async with async_session() as cap_db:
            from app.repositories.capability_repo import SQLAlchemyCapabilityRepository
            from app.services.capability_versioning import ensure_platform_version
            v = await ensure_platform_version(SQLAlchemyCapabilityRepository(cap_db))
            logger.info(f"[OK] 平台能力版本: v{v}")
    except Exception as e:
        logger.warning(f"[WARN] 平台能力版本化失败（不影响启动）: {e}", exc_info=True)


async def _startup_workers() -> None:
    """核心循环型后台 worker（异常退出自动重启）"""
    from app.ai.response_worker import ai_response_worker
    spawn_task(ai_response_worker, "ai_response_worker", restart=True)

    from app.services.memory.vector_pipeline import vector_pipeline_worker
    spawn_task(vector_pipeline_worker, "vector_pipeline_worker", restart=True)

    from app.ai.alarm import alarm_scheduler
    spawn_task(alarm_scheduler, "alarm_scheduler", restart=True)

    # 审计日志清理（每天凌晨 3 点检查）
    async def audit_cleanup_loop():
        from app.services.audit_service import cleanup_old_logs
        while True:
            await asyncio.sleep(sleep_until(3, 0))
            try:
                async with async_session() as clean_db:
                    from app.repositories.audit_repo import SQLAlchemyAuditRepository
                    result = await cleanup_old_logs(SQLAlchemyAuditRepository(clean_db))
                    if result["deleted"]:
                        logger.info(f"[OK] 审计日志清理: 删除 {result['deleted']} 条")
            except Exception as e:
                logger.warning(f"[WARN] 审计日志清理失败: {e}", exc_info=True)
    spawn_task(audit_cleanup_loop, "audit_cleanup_loop", restart=True)

    # 每日数据库备份（管理员开关 daily_backup_enabled；保留份数 daily_backup_keep）
    async def daily_backup_loop():
        from app.services.infrastructure.backup_service import create_backup, save_backup, prune_backups
        from app.services.infrastructure.system_settings_service import get_settings
        while True:
            await asyncio.sleep(sleep_until(3, 0))
            try:
                async with async_session() as backup_db:
                    s = await get_settings(backup_db)
                if not s.get("daily_backup_enabled"):
                    continue
                sql_bytes = await create_backup()
                await save_backup(sql_bytes)
                deleted = prune_backups(int(s.get("daily_backup_keep", 7) or 7))
                logger.info(f"[OK] 每日备份完成（清理 {deleted} 份过期）")
            except Exception as e:
                logger.warning(f"[WARN] 每日备份失败: {e}", exc_info=True)
    spawn_task(daily_backup_loop, "daily_backup_loop", restart=True)

    from app.services.world.world_scheduler import world_scheduler
    spawn_task(world_scheduler, "world_scheduler", restart=True)

    from app.services.memory.memory_buffer import memory_flush_worker
    spawn_task(memory_flush_worker, "memory_flush_worker", restart=True)

    # 记忆整理（每日）：低权值流水物理删除、待归档条目去重（services/memory/tidy_service.py）
    from app.services.memory.tidy_service import memory_tidy_worker
    spawn_task(memory_tidy_worker, "memory_tidy_worker", restart=True)

    from app.services.content.file_service import orphan_cleanup_worker
    spawn_task(orphan_cleanup_worker, "orphan_cleanup_worker", restart=True)

    from app.services.infrastructure.metrics_collector import metrics_flush_worker
    spawn_task(metrics_flush_worker, "metrics_flush_worker", restart=True)


async def _startup_world() -> None:
    """常驻世界恢复 + 世界商城 GitHub 自动同步（均非致命）"""
    # 恢复常驻世界（config.resident=true）——后端重启后常驻进程继续跑
    try:
        async with async_session() as restore_db:
            from app.services.world.world_resident import manager
            await manager.restore_all(restore_db)
    except Exception as e:
        logger.warning(f"[WARN] 常驻世界恢复异常: {e}")

    # 禁用后缀兜底扫描：手动拷进目录/历史遗留/解压夹带的可执行文件一律强删
    try:
        from sqlalchemy import select as sa_select
        from app.services.world.world_file_service import sweep_banned_files
        from app.models.world import World as _W
        async with async_session() as _sdb:
            _wids = list((await _sdb.execute(sa_select(_W.id))).scalars().all())
        _removed = [f"#{wid}:{p}" for wid in _wids for p in sweep_banned_files(wid)]
        if _removed:
            logger.warning(f"[WARN] 启动扫描强制删除禁用后缀文件 {len(_removed)} 个: {_removed[:10]}")
    except Exception as e:
        logger.warning(f"[WARN] 禁用后缀扫描失败（不影响启动）: {e}")

    # 世界商城 GitHub 自动同步：要打外网，绝不能挡在启动路径上（网络一慢就拖长启动）
    spawn_task(_sync_market_once, "_sync_market_once")


async def _sync_market_once() -> None:
    """启动后拉一次商城索引（后台跑）"""
    try:
        from app.services.world.market_github import get_market_config, refresh_from_github
        async with async_session() as mdb:
            cfg = await get_market_config(mdb)
        if not (cfg.get("auto_sync_enabled") and cfg.get("github_repo") and cfg.get("github_token")):
            return
        async with async_session() as mdb:
            r = await refresh_from_github(mdb)
        logger.info(f"[OK] 商城 GitHub 启动同步完成: +{r.get('added', 0)} 新增")
    except Exception as e:
        logger.warning(f"[WARN] 商城 GitHub 启动同步失败（不影响启动）: {e}")


async def _startup_federation() -> None:
    """联邦通信（v0.1.2 跨实例直连）：注册本实例 + 4 个后台任务"""
    from app.services.federation.federation_service import initialize_instance
    from app.services.federation.federation_manager import (
        federation_manager,
        federation_heartbeat,
        federation_reconnect,
        federation_profile_sync,
    )
    async with async_session() as db:
        await initialize_instance(db)
    # 连接所有已启用的对等端（在后台执行，不阻塞启动）
    spawn_task(federation_manager.connect_all_enabled_peers, "federation_manager", restart=True)
    spawn_task(federation_heartbeat, "federation_heartbeat", restart=True)
    spawn_task(federation_reconnect, "federation_reconnect", restart=True)
    spawn_task(federation_profile_sync, "federation_profile_sync", restart=True)


async def _start_service_plugins() -> None:
    """按期望状态启动所有服务插件（browser 也是其中之一，不再单独硬编码）

    期望状态存在 plugins.service_desired_running：管理员停掉的服务不会在下次重启时
    自己跑起来。内置服务插件（browser）没有 DB 行 → 缺省启动，保持原有行为。
    """
    from app.services.infrastructure.plugin_registry import PluginRegistry

    desired: dict[tuple[str, str], bool] = {}
    try:
        from app.services.plugin.config import desired_states

        desired = await desired_states()
    except Exception as e:
        # 读不到就按"都该启动"处理：宁可多启一个服务，也不要因为一张表读失败把功能全停了
        logger.warning(f"[WARN] 读取服务插件期望状态失败，按默认启动处理: {e}")

    for plugin in PluginRegistry.get_all():
        if not desired.get((plugin.id, plugin.instance), True):
            logger.info(f"[OK] 服务插件 {plugin.key} 处于停止状态，跳过启动")
            continue
        try:
            status = await plugin.get_status()
            if status.get("running"):
                logger.info(f"[OK] 服务插件已在运行: {plugin.key}")
                continue
            ok = await plugin.start()
            if ok:
                logger.info(f"[OK] 服务插件已启动: {plugin.key}")
            else:
                logger.warning(f"[WARN] 服务插件启动失败: {plugin.key}（相关功能不可用）")
        except Exception as e:
            logger.warning(f"[WARN] 服务插件启动异常: {plugin.key}: {e}", exc_info=True)


async def _stop_service_plugins() -> None:
    """停止所有服务插件（进程退出前）"""
    from app.services.infrastructure.plugin_registry import PluginRegistry

    for plugin in PluginRegistry.get_all():
        try:
            await plugin.stop()
        except Exception as e:
            logger.warning(f"[WARN] 停止服务插件失败: {plugin.id}: {e}", exc_info=True)


async def _startup_brain_and_skills() -> None:
    """薄大脑 + 技能运行时 + 时间触发器（一次性初始化）"""
    from app.services.brain.brain_controller import brain_controller
    # 大脑初始化是关键步骤，失败则中止启动
    try:
        await brain_controller.initialize()
        logger.info("[OK] 大脑控制器初始化完成")
    except Exception as e:
        logger.critical(
            f"[CRITICAL] 大脑控制器初始化失败，启动中止: {e}",
            exc_info=(type(e), e, e.__traceback__),
        )
        raise

    # 技能运行时：注册为 Skill 事件总线的派发器（自治 Skill 执行引擎）
    from app.services.skill.skill_runtime import skill_runtime
    await skill_runtime.init_dispatcher()

    # 启动时间触发器周期扫描（time 维度的执行引擎）
    from app.services.skill.trigger_sweep import trigger_sweep_worker
    spawn_task(trigger_sweep_worker, "trigger_sweep_worker", restart=True)


# ══════════════════════════════════════════════════════════════
# 生命周期
# ══════════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    started_at = time.perf_counter()   # 启动耗时：以后"为什么重启这么久"看这一行就够
    logger.info("[START] AI群聊社交网络系统启动中...")
    logger.info(f"  默认聊天模型: {settings.default_chat_model}")
    logger.info(f"  默认工作模型: {settings.default_work_model}")

    # 启动时进入自动维护模式
    maintenance.set_auto()

    await _startup_db()
    await _startup_plugins()
    await _startup_workers()
    await _startup_world()
    await _startup_federation()

    # 按期望状态启动服务插件（含共享 Chromium CDP，所有 AI 共用）
    spawn_task(_start_service_plugins, "_start_service_plugins")

    await _startup_brain_and_skills()

    logger.info("[OK] 后台 worker 已全部启动（含联邦通信）")

    # 发出系统启动完成事件
    from app.services.brain.event_bus import event_bus, EventType
    spawn_task(lambda: event_bus.emit(EventType.SYSTEM_STARTUP), "event_bus")

    # 启动完成，退出自动维护（但手动维护仍生效）
    elapsed = time.perf_counter() - started_at
    if maintenance.clear_auto():
        logger.info(
            (f"[OK] 自动维护已关闭，服务就绪（启动耗时 {elapsed:.1f}s）" if not maintenance.is_soft()
             else f"[OK] 服务就绪但软维护仍开启（启动耗时 {elapsed:.1f}s）")
        )

    yield

    # 进入关闭流程，自动维护
    logger.info("[STOP] 系统关闭，正在停止后台 worker...")
    maintenance.set_auto()

    # 发出系统关闭事件
    try:
        from app.services.brain.event_bus import event_bus, EventType
        await event_bus.emit(EventType.SYSTEM_SHUTDOWN)
    except Exception as e:
        logger.warning(f"[WARN] 系统关闭事件发送失败: {e}", exc_info=True)

    # 优雅关闭：排空记忆缓冲区
    try:
        from app.services.memory.memory_buffer import drain_buffer_on_shutdown
        await drain_buffer_on_shutdown()
    except Exception as e:
        logger.warning(f"[WARN] 记忆缓冲区排空失败: {e}", exc_info=True)

    # 先断开所有联邦连接
    try:
        from app.services.federation.federation_manager import federation_manager
        await federation_manager.disconnect_all()
    except Exception as e:
        logger.warning(f"[WARN] 联邦连接断开失败: {e}", exc_info=True)

    # 停止所有后台任务（含延迟重启任务）
    await cancel_all_tasks()

    # 停止所有服务插件（含共享 Chromium）
    await _stop_service_plugins()

    # 释放数据库连接池
    await engine.dispose()
    logger.info("[OK] 数据库连接池已释放")
    logger.info("[OK] 后台 worker 已停止")
