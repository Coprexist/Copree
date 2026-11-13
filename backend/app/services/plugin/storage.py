"""
插件存储：表由插件申请，迁移由插件自带，平台只负责记账与执行。

三条约定（其余都在此之上）：

1. **表名带归属**：`plugin_<插件id>__<表名>`（id 里的 - 换成 _）。跨 PostgreSQL/SQLite
   都成立，而且"谁的表"不用插件自报——按前缀枚举就是真相，卸载清理不会漏。
2. **迁移是 DDL 的唯一真相**：插件目录 `migrations/NNNN_*.py`，每个文件一个
   `def upgrade(conn, t)`；`conn` 是同步 Connection，`t("activity")` 给出带前缀的表名。
   只前滚；重复执行由 plugin_migrations 的 (plugin_id, version) 唯一约束挡住，
   文件被改过（checksum 不符）一律拒绝——改旧迁移等于改历史，老库新库会分叉。
3. **卸载默认保数据**：本模块只提供建/迁/查/清四个动作，删不删由管理员决定。

不要把插件表写进核心 metadata：核心的 create_all / 迁移链里不该出现插件的东西。
"""
from __future__ import annotations

import hashlib
import importlib.util
import re
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session, engine
from app.models.plugin_migration import PluginMigration

TAB_PREFIX = "plugin_"
SEP = "__"
MAX_IDENT = 63  # PostgreSQL 标识符上限
_PLUGIN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$")
_TABLE_RE = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
_MIGRATION_RE = re.compile(r"^(\d{4})_[a-z0-9_]+\.py$")

# 启动/安装时跑迁移失败的原因：管理台照这个显示，别让"表没建起来"变成一个哑掉的功能
schema_errors: dict[str, str] = {}


class StorageError(Exception):
    """插件存储约定被破坏（表名超预算、迁移被改、执行失败…）"""


def table_name(plugin_id: str, name: str) -> str:
    """插件表名 = plugin_<id>__<name>，顺手把 63 字节预算校验掉"""
    if not _PLUGIN_ID_RE.match(plugin_id or ""):
        raise StorageError(f"插件 id 不能用作表名前缀：{plugin_id!r}")
    if not _TABLE_RE.match(name or ""):
        raise StorageError(f"表名只能是 [a-z0-9_] 且不以数字开头：{name!r}")
    qualified = TAB_PREFIX + plugin_id.replace("-", "_") + SEP + name
    if len(qualified.encode("utf-8")) > MAX_IDENT:
        budget = MAX_IDENT - len(TAB_PREFIX) - len(SEP) + (len(plugin_id) - len(plugin_id.replace("-", "_")))
        raise StorageError(
            f"表名超出 {MAX_IDENT} 字节上限：{qualified}（插件 id 换成表名前缀后预算 "
            f"{MAX_IDENT - len(TAB_PREFIX) - len(SEP)} 字节，当前 {len(plugin_id)}）"
        )
    return qualified


def is_plugin_table(name: str) -> bool:
    return str(name or "").startswith(TAB_PREFIX)


def owner_of_table(name: str) -> str | None:
    """plugin_foo_bar__t → foo_bar（- 已换成 _，原名由 plugins 表反查）"""
    if not is_plugin_table(name):
        return None
    body = name[len(TAB_PREFIX):]
    if SEP not in body:
        return None
    return body.split(SEP, 1)[0]


# ══════════════════════════════════════════════════════════════
# 迁移
# ══════════════════════════════════════════════════════════════

def migration_dir(plugin_dir: Path) -> Path:
    return Path(plugin_dir) / "migrations"


def list_migrations(plugin_dir: Path) -> list[Path]:
    directory = migration_dir(plugin_dir)
    if not directory.is_dir():
        return []
    files = [p for p in directory.iterdir() if p.is_file() and _MIGRATION_RE.match(p.name)]
    files.sort(key=lambda p: p.name)
    return files


def _checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_module(path: Path, plugin_id: str, version: str):
    spec = importlib.util.spec_from_file_location(
        f"_aisc_plugin_migration_{plugin_id}_{version}", str(path))
    if spec is None or spec.loader is None:
        raise StorageError(f"迁移文件无法加载：{path.name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _applied(db: AsyncSession, plugin_id: str) -> dict[str, str]:
    rows = (await db.execute(
        text("SELECT version, checksum FROM plugin_migrations WHERE plugin_id = :pid"),
        {"pid": plugin_id},
    )).all()
    return {str(r[0]): str(r[1]) for r in rows}


async def _lock(db: AsyncSession, plugin_id: str) -> None:
    """PostgreSQL 里把同一个插件的迁移串起来（启动收敛与安装可能同时发生）"""
    if engine.dialect.name != "postgresql":
        return
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
        {"k": f"plugin_schema:{plugin_id}"},
    )


def _apply_file(session, path: Path, plugin_id: str) -> None:
    version = path.name.split("_", 1)[0]
    module = _load_module(path, plugin_id, version)
    upgrade = getattr(module, "upgrade", None)
    if not callable(upgrade):
        raise StorageError(f"{path.name} 里没有 upgrade(conn, t)")
    upgrade(session.connection(), lambda name: table_name(plugin_id, name))


async def ensure(plugin_id: str, plugin_dir: Path) -> dict[str, Any]:
    """把这个插件的迁移跑到最新；每个迁移一个事务，失败就停在这一版"""
    files = list_migrations(plugin_dir)
    result: dict[str, Any] = {"plugin_id": plugin_id, "applied": [], "skipped": 0, "error": None}
    if not files:
        schema_errors.pop(plugin_id, None)
        return result
    try:
        for path in files:
            version = path.name.split("_", 1)[0]
            checksum = _checksum(path)
            async with async_session() as db:
                await _lock(db, plugin_id)
                done = await _applied(db, plugin_id)
                if version in done:
                    if done[version] != checksum:
                        raise StorageError(
                            f"{path.name} 已应用过但文件被改过（checksum 不符）："
                            "请新增 000N 迁移，不要改旧的")
                    result["skipped"] += 1
                    continue
                await db.run_sync(lambda s: _apply_file(s, path, plugin_id))
                db.add(PluginMigration(
                    plugin_id=plugin_id, version=version, checksum=checksum, filename=path.name))
                await db.commit()
                result["applied"].append(version)
    except Exception as e:  # noqa: BLE001 —— 失败要能显示在管理台，不是吞掉
        result["error"] = str(e)
        schema_errors[plugin_id] = str(e)
        return result
    schema_errors.pop(plugin_id, None)
    return result


# ══════════════════════════════════════════════════════════════
# 盘点与清理
# ══════════════════════════════════════════════════════════════

def _table_names_sql() -> str:
    """库里有哪些插件表（只按宽前缀取候选，归属由 tables_of 精确判断）"""
    if engine.dialect.name == "postgresql":
        return (
            "SELECT tablename FROM pg_tables WHERE schemaname = current_schema() "
            "AND tablename LIKE :p ORDER BY tablename"
        )
    return "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE :p ORDER BY name"


def _owned_prefix(plugin_id: str) -> str:
    return TAB_PREFIX + plugin_id.replace("-", "_") + SEP


async def tables_of(db: AsyncSession, plugin_id: str) -> list[str]:
    """这个插件自己的表。

    为什么在 Python 里比前缀、不把表名交给 LIKE：LIKE 的 `_` 是单字符通配，
    `plugin_qq_napcat__%` 也会匹配 `plugin_qq_napcat_probe__login`——盘点会把别人的表
    算进来，purge 更会连它们一起 DROP。
    """
    rows = (await db.execute(text(_table_names_sql()), {"p": TAB_PREFIX + "%"})).all()
    own = _owned_prefix(plugin_id)
    return [str(r[0]) for r in rows if str(r[0]).startswith(own)]


async def _count(db: AsyncSession, table: str) -> int:
    if not is_plugin_table(table):
        raise StorageError(f"拒绝操作非插件表：{table}")
    return int((await db.scalar(text(f'SELECT count(*) FROM "{table}"'))) or 0)


async def _bytes(db: AsyncSession, table: str) -> int | None:
    if engine.dialect.name != "postgresql":
        return None
    return int((await db.scalar(text("SELECT pg_total_relation_size(:t)"), {"t": table})) or 0)


async def assets(db: AsyncSession, plugin_id: str) -> dict[str, Any]:
    """这个插件在库里留了什么：表、行数、占用；也给管理台算"保留了多少数据" """
    tables = await tables_of(db, plugin_id)
    detail = []
    total_rows = 0
    total_bytes = 0
    for table in tables:
        rows = await _count(db, table)
        size = await _bytes(db, table)
        detail.append({"table": table, "rows": rows, "bytes": size})
        total_rows += rows
        total_bytes += size or 0
    return {
        "plugin_id": plugin_id,
        "tables": detail,
        "rows": total_rows,
        "bytes": total_bytes,
        "error": schema_errors.get(plugin_id),
    }


async def purge(db: AsyncSession, plugin_id: str) -> dict[str, Any]:
    """彻底清掉这个插件的数据：表 + 记账行。调用方负责插件目录与 plugin_data 目录。"""
    tables = await tables_of(db, plugin_id)
    for table in tables:
        if not is_plugin_table(table):
            raise StorageError(f"拒绝 DROP 非插件表：{table}")
        await db.execute(text(f'DROP TABLE IF EXISTS "{table}" CASCADE'))
    await db.execute(
        text("DELETE FROM plugin_migrations WHERE plugin_id = :pid"), {"pid": plugin_id})
    await db.commit()
    schema_errors.pop(plugin_id, None)
    return {"plugin_id": plugin_id, "dropped": tables}


def _installed_dirs() -> dict[str, Path]:
    """磁盘上装了哪些插件（用户目录优先于内置目录，同名以内置为准的规则不在这里重复）"""
    from app.services.plugin import catalog

    found: dict[str, Path] = {}
    for root in (Path(catalog.USER_PLUGIN_DIR), Path(catalog.BUILTIN_PLUGIN_DIR)):
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if (child / "plugin.json").is_file():
                found.setdefault(child.name, child)
    return found


def installed_ids() -> list[str]:
    """磁盘上装了哪些插件（管理台盘点、孤儿数据判定都用这一处）"""
    return sorted(_installed_dirs().keys())


async def plugins_with_data(db: AsyncSession) -> list[str]:
    """库里有数据的插件 id：跑过迁移的 + 按前缀能认出归属的表"""
    ids: set[str] = set()
    rows = (await db.execute(text("SELECT DISTINCT plugin_id FROM plugin_migrations"))).all()
    ids |= {str(r[0]) for r in rows}
    names = (await db.execute(text(_table_names_sql()), {"p": TAB_PREFIX + "%"})).all()
    for (name,) in names:
        owner = owner_of_table(str(name))
        if owner:
            ids.add(owner)
    return sorted(ids)


async def ensure_installed(plugin_id: str) -> dict[str, Any] | None:
    """插件目录存在就把它迁到最新；没有 migrations/ 的插件原样跳过"""
    plugin_dir = _installed_dirs().get(plugin_id)
    if plugin_dir is None:
        return None
    return await ensure(plugin_id, plugin_dir)


async def sync_all() -> dict[str, dict[str, Any]]:
    """启动时把装了迁移的插件逐个收敛：代码加载之前表就要在，否则第一条查询就炸。

    单个插件失败不影响别的插件；失败原因留在 schema_errors 里给管理台看。
    """
    result: dict[str, dict[str, Any]] = {}
    for plugin_id, plugin_dir in _installed_dirs().items():
        if not list_migrations(plugin_dir):
            continue
        result[plugin_id] = await ensure(plugin_id, plugin_dir)
    return result


async def had_data(plugin_id: str) -> bool:
    """这个插件是否留下过数据（表或记账行）——卸载对话框是否要提示的判据"""
    async with async_session() as db:
        return bool(await tables_of(db, plugin_id)) or bool(await _applied(db, plugin_id))
