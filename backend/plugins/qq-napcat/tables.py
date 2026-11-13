"""qq-napcat 自己表（plugin_qq_napcat__*）的查询侧声明

DDL 的唯一来源是 migrations/（这里不建表）；本文件只把查询要用的列声明一次，
免得列名散进每条 f-string。表名与约束名都经 T 前缀化，卸载清理不会认错归属。
"""
import sqlalchemy as sa

from app.services.plugin.tables import PluginTables

T = PluginTables("qq-napcat")

ACCOUNTS = T.table(
    "accounts",
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("instance", sa.String(40), nullable=False),
    sa.Column("uin", sa.String(20), nullable=False, server_default=""),
    sa.Column("nick", sa.String(80), nullable=False, server_default=""),
    sa.Column("avatar", sa.String(400), nullable=False, server_default=""),
    sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    sa.UniqueConstraint("instance", name=T.name("accounts_instance_key")),
)
