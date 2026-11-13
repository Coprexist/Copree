"""插件表的查询侧声明：名字前缀化、约束名一起前缀化、写入口跟着库方言走。"""
import sqlalchemy as sa


def test_table_name_carries_the_plugin_prefix():
    from app.services.plugin.tables import PluginTables

    T = PluginTables("qq-napcat")
    table = T.table("notes", sa.Column("id", sa.Integer, primary_key=True))

    assert table.name == "plugin_qq_napcat__notes"
    assert T.name("notes") == table.name


def test_constraint_names_are_prefixed_too():
    from app.services.plugin.tables import PluginTables

    T = PluginTables("my-plugin")
    table = T.table(
        "notes",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("body", sa.Text, nullable=False),
        sa.UniqueConstraint("body", name=T.name("notes_key")),
    )

    assert table.name == "plugin_my_plugin__notes"
    assert [c.name for c in table.constraints if isinstance(c, sa.UniqueConstraint)] == [
        "plugin_my_plugin__notes_key",
    ]


def test_shared_metadata_keeps_tables_together():
    from app.services.plugin.tables import PluginTables

    T = PluginTables("my-plugin")
    a = T.table("a", sa.Column("id", sa.Integer, primary_key=True))
    b = T.table("b", sa.Column("id", sa.Integer, primary_key=True))

    assert set(T.metadata.tables) == {a.name, b.name}


def test_insert_follows_the_backend_dialect():
    """upsert 构造器要跟着库走：写死 PG 的 insert 会让安装包（SQLite）一执行就炸"""
    from app.services.plugin import tables as mod

    T = mod.PluginTables("my-plugin")
    table = T.table("notes", sa.Column("id", sa.Integer, primary_key=True))

    class _SqliteEngine:
        class dialect:
            name = "sqlite"

    real = mod.engine
    try:
        mod.engine = _SqliteEngine
        assert type(T.insert(table)).__module__.startswith("sqlalchemy.dialects.sqlite")
    finally:
        mod.engine = real
    assert type(T.insert(table)).__module__.startswith("sqlalchemy.dialects.postgresql")
