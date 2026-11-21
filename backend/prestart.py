"""预启动：只在"迁移集合变了"时才真的跑 Alembic。

原来每次启动都无条件 `alembic upgrade head`——已经是最新时也要付 2.1 秒（加载 env.py、
解析 100+ 个迁移文件、连库比对）。而这件事的判断依据其实很简单：**迁移文件集合有没有变过**。
集合没变过就不可能有未应用的迁移；变了就必跑一次。所以把集合指纹记在 data 目录，一致就跳过。

三条保守规则（宁可慢一次，不能漏迁移）：
- 没有 alembic_version 表（全新库）→ 照旧跳过，交给 bootstrap 的 create_all + stamp；
- 指纹读不出/写不进 → 每次都跑 alembic（退化成老行为）；
- 指纹一致但版本号对不上 → 跑 alembic（有人手动 downgrade 过）。

向量维度自检搬去了 bootstrap 的后台任务：它失败也不阻塞启动，没理由卡在这里。
SQLite: 跳过（由 bootstrap.py 的 create_all 处理）。
"""
import asyncio
import hashlib
import os
import subprocess
import sys
import time
from pathlib import Path

_T0 = time.perf_counter()


def _say(message: str, *, error: bool = False) -> None:
    """带时刻与相对耗时的一行。

    stdout 是块缓冲：几行 print 会被一起 flush，docker 采集到的外部时间戳会挤在同一毫秒
    （实测"执行迁移"与"迁移完成"只差 29 微秒，那是假的）。所以自己把"走到第几秒"写进正文。
    """
    stamp = time.strftime("%H:%M:%S")
    line = f"[prestart {stamp} +{time.perf_counter() - _T0:.2f}s] {message}"
    print(line, file=sys.stderr if error else sys.stdout, flush=True)


async def _probe(url: str) -> tuple[bool, str]:
    """(有没有 alembic_version 表, 当前版本号)"""
    import asyncpg

    from app.db_migrate import has_alembic_version_async
    from scripts._shared import parse_db_url

    cfg = parse_db_url(url)
    conn = await asyncpg.connect(**cfg)
    try:
        if not await has_alembic_version_async(conn):
            return False, ""
        row = await conn.fetchrow("SELECT version_num FROM alembic_version LIMIT 1")
        return True, (str(row["version_num"]) if row else "")
    finally:
        await conn.close()


def _fingerprint(backend_dir: Path) -> str:
    """迁移集合的指纹：文件名 + 大小 + mtime。集合没变过 = 不会有未应用的迁移。"""
    versions = backend_dir / "alembic" / "versions"
    try:
        parts = sorted(
            f"{p.name}:{p.stat().st_size}:{int(p.stat().st_mtime)}"
            for p in versions.glob("*.py")
        )
    except OSError:
        return ""
    if not parts:
        return ""
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:16]


def _marker(backend_dir: Path) -> Path:
    """指纹落地在 data 目录（持久卷）：容器重建、代码不变 → 依然跳过。"""
    return backend_dir / "data" / ".alembic_applied"


def run() -> None:
    url = os.getenv("DATABASE_URL") or os.getenv("DATABASE_URL_SYNC", "")
    if not url:
        _say("未设置 DATABASE_URL，跳过迁移")
        return

    if "postgresql" not in url:
        _say("SQLite 模式，跳过 prestart 迁移")
        return

    backend_dir = Path(__file__).resolve().parent

    has_version, current = asyncio.run(_probe(url))
    if not has_version:
        _say("全新数据库，跳过 Alembic（由 bootstrap.py 处理）")
        return

    fingerprint = _fingerprint(backend_dir)
    marker = _marker(backend_dir)
    try:
        recorded = marker.read_text(encoding="utf-8").strip() if marker.exists() else ""
    except OSError:
        recorded = ""

    if fingerprint and recorded == f"{fingerprint} {current}":
        _say(f"迁移集合未变（当前 {current}），跳过 alembic upgrade")
        return

    _say(f"执行 Alembic 迁移（当前 {current or '未知'}，指纹 {fingerprint or '无'}）...")
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(backend_dir),
    )
    if result.returncode != 0:
        _say("Alembic 迁移失败", error=True)
        sys.exit(result.returncode)
    _say("Alembic 迁移完成")

    # 记下"这次跑完之后"的版本：下次启动据此跳过
    try:
        _has, after = asyncio.run(_probe(url))
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(f"{fingerprint} {after}", encoding="utf-8")
    except Exception as e:  # noqa: BLE001 —— 写不进指纹只是下次重跑，不影响启动
        _say(f"迁移指纹写入失败（下次仍会重跑 alembic）: {e}", error=True)


if __name__ == "__main__":
    run()
