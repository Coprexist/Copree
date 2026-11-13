"""插件自带的表：申请、迁移、幂等、改历史被拒、清空。

这套机制的价值全在边界上：重复安装不能重跑（表已存在就报错）、改了旧迁移必须拒绝
（否则老库新库分叉）、卸载默认保数据但要能一键清干净。用例就钉这些。

临时目录用 tempfile 而不是 pytest 的 tmp_path：容器里的最小运行器只解析 conftest 的
fixture（见 tests/run_without_pytest.py），用例要两边都能跑。
"""
import shutil
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.anyio


MIGRATION_0001 = '''
import sqlalchemy as sa


def upgrade(conn, t):
    meta = sa.MetaData()
    sa.Table(
        t("note"), meta,
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("body", sa.Text, nullable=False, server_default=""),
    )
    meta.create_all(conn)
'''


def _root():
    return Path(tempfile.mkdtemp(prefix="plugin-storage-test-"))


def _make_plugin(root: Path, plugin_id: str = "demo-plugin") -> Path:
    plugin_dir = root / plugin_id
    (plugin_dir / "migrations").mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text('{"id": "%s"}' % plugin_id, encoding="utf-8")
    (plugin_dir / "migrations" / "0001_init.py").write_text(MIGRATION_0001, encoding="utf-8")
    return plugin_dir


async def test_first_run_creates_tables_and_records_version(migrated_db):
    from app.database import async_session
    from app.services.plugin import storage

    pid = "demo-first"
    table = storage.table_name(pid, "note")
    root = _root()
    try:
        plugin_dir = _make_plugin(root, pid)
        result = await storage.ensure(pid, plugin_dir)
        assert result["error"] is None
        assert result["applied"] == ["0001"]

        async with async_session() as db:
            assert await storage.tables_of(db, pid) == [table]
            rows = (await db.execute(text(
                "SELECT version, filename FROM plugin_migrations WHERE plugin_id = :p"
            ), {"p": pid})).all()
            assert [(r[0], r[1]) for r in rows] == [("0001", "0001_init.py")]

            # 表真的能用，盘点能数到行
            await db.execute(text(f'INSERT INTO "{table}" (body) VALUES (:b)'), {"b": "hi"})
            total = await storage.assets(db, pid)
            assert total["rows"] == 1
            assert total["tables"][0]["table"] == table
            assert total["error"] is None
            await db.rollback()
    finally:
        shutil.rmtree(root, ignore_errors=True)


async def test_second_run_is_a_noop(migrated_db):
    from app.services.plugin import storage

    root = _root()
    try:
        plugin_dir = _make_plugin(root, "demo-twice")
        await storage.ensure("demo-twice", plugin_dir)
        again = await storage.ensure("demo-twice", plugin_dir)
        assert again["applied"] == []
        assert again["skipped"] == 1
        assert again["error"] is None
    finally:
        shutil.rmtree(root, ignore_errors=True)


async def test_editing_an_applied_migration_is_rejected(migrated_db):
    """改历史迁移等于改历史：老库已经按旧文件建过表，静默通过会让两边分叉"""
    from app.services.plugin import storage

    root = _root()
    try:
        plugin_dir = _make_plugin(root, "demo-edit")
        await storage.ensure("demo-edit", plugin_dir)
        (plugin_dir / "migrations" / "0001_init.py").write_text(
            MIGRATION_0001 + "\n# 事后改了一下\n", encoding="utf-8")

        result = await storage.ensure("demo-edit", plugin_dir)
        assert result["error"], "改过已应用的迁移必须报错"
        assert "checksum" in result["error"] or "改过" in result["error"]
        assert storage.schema_errors.get("demo-edit")
    finally:
        shutil.rmtree(root, ignore_errors=True)


async def test_purge_drops_tables_and_bookkeeping(migrated_db):
    from app.database import async_session
    from app.services.plugin import storage

    root = _root()
    try:
        pid = "demo-purge"
        table = storage.table_name(pid, "note")
        plugin_dir = _make_plugin(root, pid)
        await storage.ensure(pid, plugin_dir)
        async with async_session() as db:
            await db.execute(text(f'INSERT INTO "{table}" (body) VALUES (:b)'), {"b": "x"})
            await db.commit()
            assert await storage.had_data(pid) is True

            result = await storage.purge(db, pid)
            assert result["dropped"] == [table]
            assert await storage.tables_of(db, pid) == []
        async with async_session() as db:
            left = (await db.execute(text(
                "SELECT count(*) FROM plugin_migrations WHERE plugin_id = :p"
            ), {"p": pid})).scalar()
            assert left == 0
            assert await storage.had_data(pid) is False
    finally:
        shutil.rmtree(root, ignore_errors=True)


async def test_prefix_sibling_is_not_counted_as_its_own(migrated_db):
    """id 前缀相同的另一个插件：盘点与清理都不能把它算进来（LIKE 的 `_` 是通配符）"""
    from app.database import async_session
    from app.services.plugin import storage

    root = _root()
    try:
        base, sibling = "demo-share", "demo-share-extra"
        await storage.ensure(base, _make_plugin(root, base))
        await storage.ensure(sibling, _make_plugin(root, sibling))
        async with async_session() as db:
            mine = await storage.tables_of(db, base)
            assert mine == [storage.table_name(base, "note")]
            assert await storage.tables_of(db, sibling) == [storage.table_name(sibling, "note")]
            await storage.purge(db, base)
        async with async_session() as db:
            assert await storage.tables_of(db, base) == []
            assert await storage.tables_of(db, sibling) == [storage.table_name(sibling, "note")]
            await storage.purge(db, sibling)
    finally:
        shutil.rmtree(root, ignore_errors=True)


async def test_table_name_prefix_and_budget():
    from app.services.plugin import storage

    assert storage.table_name("bilibili", "activity") == "plugin_bilibili__activity"
    assert storage.table_name("my-plugin", "subs") == "plugin_my_plugin__subs"
    with pytest.raises(storage.StorageError):
        storage.table_name("bilibili", "Bad-Name")
    with pytest.raises(storage.StorageError):
        storage.table_name("x" * 60, "y" * 9)   # 前缀 + 名字超过 63 字节


async def test_no_migration_dir_is_fine(migrated_db):
    """没有 migrations/ 的插件不该被当成错误：它只是不要表"""
    from app.services.plugin import storage

    root = _root()
    try:
        plugin_dir = root / "demo-plain"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.json").write_text('{"id": "demo-plain"}', encoding="utf-8")
        result = await storage.ensure("demo-plain", plugin_dir)
        assert result == {"plugin_id": "demo-plain", "applied": [], "skipped": 0, "error": None}
    finally:
        shutil.rmtree(root, ignore_errors=True)
