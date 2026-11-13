"""qq-napcat 的 tables.py 与迁移不漂移：查询侧列集合 == 库里实际列集合

DDL 只有 migrations/ 一个来源，tables.py 只是查询侧声明；两份声明一旦漂移，
后果是运行时才炸（列刚加进迁移、查询里还没有）。这条用例把它挡在测试里。
"""
from pathlib import Path

import pytest
import sqlalchemy as sa

PLUGIN_DIR = Path(__file__).resolve().parents[1] / "plugins" / "qq-napcat"


def _load_plugin():
    from app.services.plugin import api, packages

    api.set_current_plugin("qq-napcat")
    try:
        return packages.load("qq-napcat", PLUGIN_DIR)
    finally:
        api.set_current_plugin(None)


@pytest.mark.asyncio
async def test_tables_py_matches_the_migrated_table(migrated_db):
    from app.database import async_session
    from app.services.plugin import storage

    schema = await storage.ensure("qq-napcat", PLUGIN_DIR)
    assert schema["error"] is None

    table = _load_plugin().accounts.ACCOUNTS
    async with async_session() as db:
        actual = {
            str(r[0]) for r in (await db.execute(sa.text(
                "SELECT column_name FROM information_schema.columns WHERE table_name = :t"
            ), {"t": table.name})).all()
        }
    assert actual == {c.name for c in table.columns}, (
        f"tables.py 声明了 {sorted(c.name for c in table.columns)}，"
        f"库里是 {sorted(actual)}"
    )
