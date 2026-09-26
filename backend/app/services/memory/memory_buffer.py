"""
记忆批量写入缓冲区 + 延迟归档

设计目的：
  替代原有的同步逐条写入模式，减少 embedding API 调用频率和 DB 事务次数。

架构：
  PendingMemory 数据类 → pending_memories (asyncio.Queue, maxsize=500)
      ↓
  memory_flush_worker (后台 asyncio 任务)
      触发条件: 5 条阈值 OR 30 秒超时
      ↓
  _batch_write_memories(): 并发 embedding → 批量 INSERT → 单次 commit

边界条件（已知限制）：
  - 缓冲区在进程内存中，docker restart / OOM kill / 电源故障会导致未 flush 的记忆丢失
  - 缓解：阈值 5 条（平均驻留 < 15 秒）+ 30 秒超时兜底 + 失败重入队
  - 对"生命感"影响低：崩溃是运维事件，正常运行时 30 秒内必然落盘
  - 未来增强：Redis 持久化缓冲区 + 优雅关闭 drain

延迟归档：
  - 自动提取的偏好/简短信息标记 low_value=True，写入时 status='pending_archive'
  - 归并由 services/memory/tidy_service.py 每日处理：同名只留一条，独特的转正为 active
"""
import asyncio
import time
import logging
from dataclasses import dataclass, field

from app.repositories.memory_repo import MemoryRepository, SQLAlchemyMemoryRepository
from sqlalchemy.ext.asyncio import AsyncSession
from app.utils.pure.memory_weight import default_weight
logger = logging.getLogger(__name__)

def _ensure_repo(db_or_repo):
    """兼容旧调用：传入 AsyncSession 时包装为 SQLAlchemyMemoryRepository。"""
    if isinstance(db_or_repo, AsyncSession):
        return SQLAlchemyMemoryRepository(db_or_repo)
    return db_or_repo



# ══════════════════════════════════════════════════════════════
# 数据结构
# ══════════════════════════════════════════════════════════════

@dataclass
class PendingMemory:
    """待写入的 memory 条目"""
    agent_id: int
    group_id: int | None
    title: str
    content: str
    scope: str                     # "private" | "group" | "cross_user"
    api_base_url: str
    api_key: str | None
    trigger_user_id: int | None    # v0.1.3 per-user 隔离
    # 类型、设定权值与焦段锚点（v1.1）：weight=0 表示用该类型的默认权值
    mem_type: str = "daily"
    weight: int = 0
    session_foci: list = field(default_factory=list)
    semantic_foci: list = field(default_factory=list)
    ai_type: str = "resonance"     # "resonance" | "general" | "semi_general"
    source: str = "tool"           # "tool" | "auto_extract"
    low_value: bool = False        # True=自动提取的简短偏好，待归档
    created_at: float = field(default_factory=time.monotonic)


# ══════════════════════════════════════════════════════════════
# 缓冲区 + 控制
# ══════════════════════════════════════════════════════════════

# 核心缓冲区（asyncio.Queue 线程安全，自带背压）
pending_memories: asyncio.Queue[PendingMemory] = asyncio.Queue(maxsize=500)

# 批量 flush 控制
_flush_event: asyncio.Event = asyncio.Event()
_flush_threshold: int = 5          # 达到 5 条触发批量写入
_flush_timeout: float = 30.0       # 30 秒超时强制 flush


# ══════════════════════════════════════════════════════════════
# 公共接口
# ══════════════════════════════════════════════════════════════

async def enqueue_memory(
    agent_id: int,
    title: str,
    content: str,
    scope: str = "private",
    group_id: int | None = None,
    api_base_url: str = "https://api.deepseek.com",
    api_key: str | None = None,
    trigger_user_id: int | None = None,
    ai_type: str = "resonance",
    source: str = "tool",
    low_value: bool = False,
    mem_type: str = "daily",
    weight: int = 0,
    session_foci: list | None = None,
    semantic_foci: list | None = None,
) -> None:
    """
    将记忆加入缓冲区，不立即写 DB。

    参数:
        low_value: True 表示自动提取的简短偏好，入库时 status='pending_archive'
    """
    mem = PendingMemory(
        agent_id=agent_id,
        group_id=group_id,
        title=title,
        content=content,
        scope=scope,
        api_base_url=api_base_url,
        api_key=api_key,
        trigger_user_id=trigger_user_id,
        ai_type=ai_type,
        source=source,
        low_value=low_value,
        mem_type=mem_type,
        weight=weight,
        session_foci=list(session_foci or []),
        semantic_foci=list(semantic_foci or []),
    )
    try:
        pending_memories.put_nowait(mem)
        logger.debug(f"📝 记忆入缓冲区: {title[:50]} ({'低价值' if low_value else '普通'})")
    except asyncio.QueueFull:
        logger.warning("记忆缓冲区已满（500 条），丢弃最旧条目")
        try:
            pending_memories.get_nowait()
            pending_memories.put_nowait(mem)
        except Exception:
            pass

    # 达到阈值立即触发 flush
    if pending_memories.qsize() >= _flush_threshold:
        _flush_event.set()


async def drain_buffer_on_shutdown():
    """
    优雅关闭时强制 flush 缓冲区中所有未落盘的记忆。
    由 main.py shutdown handler 调用。
    """
    batch: list[PendingMemory] = []
    while not pending_memories.empty():
        try:
            batch.append(pending_memories.get_nowait())
        except asyncio.QueueEmpty:
            break

    if not batch:
        return

    logger.info(f"🔄 优雅关闭：强制 flush {len(batch)} 条缓冲记忆...")
    from app.database import async_session
    async with async_session() as db:
        try:
            await _batch_write_memories(db, batch)
            await db.commit()
            logger.info(f"✅ 优雅关闭 flush 完成: {len(batch)} 条")
        except Exception as e:
            logger.error(f"❌ 优雅关闭 flush 失败: {e}")
            # 无法恢复，放弃这批记忆（进程即将退出）


# ══════════════════════════════════════════════════════════════
# 后台 Worker
# ══════════════════════════════════════════════════════════════

async def memory_flush_worker():
    """
    后台 worker：定期或按阈值触发批量记忆写入。
    在 main.py lifespan 中通过 asyncio.create_task 启动。
    """
    logger.info("📝 记忆批量写入 worker 已启动（阈值=%d 条, 超时=%d 秒）",
                _flush_threshold, _flush_timeout)
    while True:
        try:
            # 等待触发条件：达到阈值 OR 超时
            try:
                await asyncio.wait_for(_flush_event.wait(), timeout=_flush_timeout)
            except asyncio.TimeoutError:
                pass  # 超时也触发 flush
            _flush_event.clear()

            # 收集所有待处理条目
            batch: list[PendingMemory] = []
            while not pending_memories.empty():
                try:
                    batch.append(pending_memories.get_nowait())
                except asyncio.QueueEmpty:
                    break

            if not batch:
                continue

            logger.info(f"📝 批量 flush {len(batch)} 条记忆...")
            from app.database import async_session
            async with async_session() as db:
                try:
                    await _batch_write_memories(db, batch)
                    await db.commit()
                    logger.info(f"✅ 批量写入 {len(batch)} 条记忆成功")
                except Exception as e:
                    logger.error(f"❌ 批量写入记忆失败，重新入队: {e}")
                    # 失败重新入队（事务安全）
                    for mem in batch:
                        try:
                            pending_memories.put_nowait(mem)
                        except asyncio.QueueFull:
                            logger.warning(f"重入队失败：丢弃记忆 {mem.title[:50]}")
                    await asyncio.sleep(5)  # 短暂延迟后重试

            for _ in batch:
                pending_memories.task_done()

        except asyncio.CancelledError:
            logger.info("记忆 flush worker 正在关闭...")
            break
        except Exception as e:
            logger.error(f"记忆 flush worker 异常: {e}", exc_info=True)
            await asyncio.sleep(1)


# ══════════════════════════════════════════════════════════════
# 批量写入核心
# ══════════════════════════════════════════════════════════════

async def _batch_write_memories(db, batch: list[PendingMemory]):
    """
    批量写入记忆：
    1. 并发获取所有 embedding
    2. 批量构建 RoughMemory + DetailMemory
    3. 一次 flush 提交（事务安全：全部成功或全部回滚）
    """
    db = _ensure_repo(db)
    from app.models.memory import RoughMemory, DetailMemory
    from app.services.infrastructure.metrics_collector import metrics

    t0 = time.monotonic()

    # Step 1: 并发 embedding（不阻塞彼此）
    embedding_tasks = []
    for mem in batch:
        embedding_tasks.append(_get_embedding_safe(mem.title, mem.api_base_url, mem.api_key))
    embeddings = await asyncio.gather(*embedding_tasks)

    # Step 2: 批量构建 ORM 对象
    roughs: list[RoughMemory] = []
    details: list[DetailMemory] = []
    for mem, embedding in zip(batch, embeddings):
        # 确定 user_id（per-user 记忆隔离）
        memory_user_id = None
        if mem.ai_type in ("general", "semi_general") and mem.trigger_user_id:
            memory_user_id = mem.trigger_user_id

        rough = RoughMemory(
            owner_type="ai",
            owner_id=mem.agent_id,
            title=mem.title,
            embedding=embedding,
            scope=mem.scope,
            group_id=mem.group_id if mem.scope == "group" else None,
            user_id=memory_user_id,
            status="pending_archive" if mem.low_value else "active",
            # 自动提取的流水一律最低档；其余按 AI 给的权值，没给就用该类型的默认
            value_score=1 if mem.low_value else (mem.weight or default_weight(mem.mem_type)),
            mem_type="daily" if mem.low_value else mem.mem_type,
            session_foci=list(mem.session_foci or []),
            semantic_foci=list(mem.semantic_foci or []),
        )
        db.add(rough)
        roughs.append(rough)

    await db.flush()  # 获取 rough.id

    for rough, mem in zip(roughs, batch):
        detail = DetailMemory(
            rough_id=rough.id,
            content=mem.content,
        )
        db.add(detail)
        details.append(detail)

    await db.flush()

    # v0.1.4: 记录批量写入延迟
    elapsed = time.monotonic() - t0
    try:
        await metrics.record_memory_write(elapsed)
    except Exception:
        pass

    logger.info(f"📝 批量写入 rough={len(roughs)}, detail={len(details)}, 耗时={elapsed:.2f}s")


async def _get_embedding_safe(title: str, api_base_url: str, api_key: str | None) -> list[float] | None:
    """安全获取 embedding，失败返回 None（不阻塞批量写入）"""
    try:
        from app.utils.embedding import get_embedding
        return await get_embedding(title, api_base_url=api_base_url, api_key=api_key)
    except Exception as e:
        logger.warning(f"Embedding 失败（记忆仍可文本检索）: {e}")
        return None


# ══════════════════════════════════════════════════════════════
# 延迟归档
# ══════════════════════════════════════════════════════════════
