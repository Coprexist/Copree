"""
预启动迁移脚本 — 执行 Alembic schema 迁移。

PostgreSQL: 检查 alembic_version 表是否存在。
  - 存在 → 旧库，执行 alembic upgrade head
  - 不存在 → 新库，跳过（由 bootstrap.py 的 create_all + stamp head 处理）

SQLite: 跳过（由 bootstrap.py 的 create_all 处理）
"""
import asyncio
import os
import subprocess
import sys


async def _check_alembic_version(url: str) -> bool:
    """检查 alembic_version 表是否存在（asyncpg）"""
    from app.db_migrate import has_alembic_version_async
    from scripts._shared import parse_db_url
    import asyncpg

    cfg = parse_db_url(url)
    conn = await asyncpg.connect(**cfg)
    try:
        return await has_alembic_version_async(conn)
    finally:
        await conn.close()


def run():
    url = os.getenv("DATABASE_URL") or os.getenv("DATABASE_URL_SYNC", "")
    if not url:
        print("Prestart: 未设置 DATABASE_URL，跳过迁移")
        return

    if "postgresql" not in url:
        print("Prestart: SQLite 模式，跳过 prestart 迁移")
        return

    # 检查 alembic_version 表是否存在
    has_version = asyncio.run(_check_alembic_version(url))

    if not has_version:
        print("Prestart: 全新数据库，跳过 Alembic（由 bootstrap.py 处理）")
        return

    # 旧库：执行 Alembic 迁移
    print("Prestart: 执行 Alembic 迁移...")
    backend_dir = os.path.dirname(os.path.abspath(__file__))
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=backend_dir,
    )
    if result.returncode != 0:
        print("Prestart: Alembic 迁移失败", file=sys.stderr)
        sys.exit(result.returncode)
    print("Prestart: Alembic 迁移完成")
    _check_embedding_dimension(url)


def _check_embedding_dimension(url: str) -> None:
    """向量维度自检：三者不一致时写入会静默失败（2026-09-25 事故），这里必须响。"""
    async def _run():
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app.services.infrastructure.embedding_config_service import (
            check_dimension_consistency,
        )

        engine = create_async_engine(url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as db:
            return await check_dimension_consistency(db)

    try:
        problems = asyncio.run(_run())
    except Exception as e:  # 自检失败不阻塞启动
        print(f"Prestart: 向量维度自检跳过（{e}）")
        return
    if not problems:
        print("Prestart: 向量维度自检通过")
        return
    print("Prestart: [ERROR] 向量维度不一致，记忆写入会失败：" + "；".join(problems), file=sys.stderr)
    print("Prestart: 修法：EMBEDDING_DIMENSION 必须等于表列维度与 embedding 模型输出维度"
          "（本地 nomic-embed-text = 768）", file=sys.stderr)


if __name__ == "__main__":
    run()
