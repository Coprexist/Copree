"""
数据库连接管理模块
使用 SQLAlchemy 2.0 异步引擎，存储后端由 DB_BACKEND 选择（postgres | sqlite）。
"""
import logging

from sqlalchemy import text
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from app.config import settings
from app.db_providers import get_provider

logger = logging.getLogger(__name__)

# 当前生效的存储后端
provider = get_provider()

# 异步引擎
# 注意：SQLite(aiosqlite) 使用 NullPool，不接受 pool_size/max_overflow/pool_pre_ping 参数
if provider.name == "postgres":
    engine = create_async_engine(
        provider.async_engine_url(),
        pool_size=10,
        max_overflow=40,
        pool_pre_ping=True,
        echo=False,
    )
else:
    engine = create_async_engine(
        provider.async_engine_url(),
        echo=False,
    )

# 会话工厂
async_session = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """SQLAlchemy 声明式基类"""
    pass


@asynccontextmanager
async def work_session():
    """一段工作的会话：出块即提交，抛错回滚。HTTP（get_db）与后台轮次共用这一份。

    提交是「这段工作算数」的唯一开关：散在调用方手里时，忘一处就把已经发出去的消息一起丢掉
    （2026-10-04 决策技能代发那四条）。并进开会话这一步之后，不开这个口子就跑不了。

    **不是 savepoint**：每次调用是新会话 + 新事务。嵌套时内层独立——内层提交外层回滚也带不走，
    内层回滚也不影响外层；要"一起成一起败"就把同一个 session 往下传，别用嵌套。
    后台 fire-and-forget 的 create_task 是**重叠**而非嵌套：外层不等内层。速查 docs/CODE_WIKI.md §5.3。
    """
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_db() -> AsyncSession:
    """FastAPI 依赖注入：获取数据库会话（HTTP 形态的 work_session）"""
    async with work_session() as session:
        yield session


async def check_db_connection() -> bool:
    """检查数据库连接是否正常"""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as e:
        logger.error(f"数据库连接失败: {e}")
        return False


async def dispose_db() -> None:
    """释放数据库连接池（应用关闭时调用）"""
    await engine.dispose()
    logger.info("数据库连接池已释放")
