"""插件自带表的查询侧声明。

DDL 的唯一真相仍是插件目录下的 `migrations/`（见 docs/plugin-dev/plugin-storage.md）；
这里管的只有两件事：**把查询要用的列声明一次**，以及**给出与库方言无关的写入口**。

用法（插件自己的 `tables.py`）：

    from app.services.plugin.tables import PluginTables

    T = PluginTables("my-channel")
    ACCOUNTS = T.table(
        "accounts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("uin", sa.String(32), nullable=False),
    )

查询走 SQLAlchemy 表达式（`select(ACCOUNTS.c.uin)`），别再把列名写进 f-string；
upsert 走 `T.insert(ACCOUNTS).on_conflict_do_update(...)`，不要直接 import 某个方言的 insert
（那会把插件钉死在 PostgreSQL 上，而安装包是 SQLite）。

约定：
- 表名一律经 `table_name()` 前缀化，卸载插件时按前缀清理，不会误删核心表；
- 核心不 import 插件模块：这个类只认识 plugin_id 与列定义；
- 一个插件一份 `tables.py`，是它自己能读写成哪些列的唯一清单（DDL 与它漂移由测试兜）。
"""
from __future__ import annotations

from typing import Any

import sqlalchemy as sa

from app.database import engine
from app.services.plugin.storage import table_name


class PluginTables:
    """某个插件的表工厂：名字前缀化，列定义集中在一处。"""

    def __init__(self, plugin_id: str) -> None:
        self.plugin_id = plugin_id
        self.metadata = sa.MetaData()

    def name(self, table: str) -> str:
        """逻辑表名 → 带前缀的真实表名（与迁移文件里的 t() 同一套规则）。"""
        return table_name(self.plugin_id, table)

    def table(self, name: str, *columns: Any, **kwargs: Any) -> sa.Table:
        return sa.Table(self.name(name), self.metadata, *columns, **kwargs)

    def insert(self, table: sa.Table):
        """按当前库的方言给出 insert 构造器（两种方言的 upsert 语法同形、构造器不同）。

        插件写 `T.insert(TABLE).on_conflict_do_update(...)` 就同时成立；直接
        `from sqlalchemy.dialects.postgresql import insert` 会让安装包（SQLite）一执行就炸。
        """
        if engine.dialect.name == "sqlite":
            from sqlalchemy.dialects.sqlite import insert
        else:
            from sqlalchemy.dialects.postgresql import insert
        return insert(table)
