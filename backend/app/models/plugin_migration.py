"""
插件迁移记账：哪些插件的哪些迁移已经跑过。

这是「范式接口」那一类核心表：它不认识任何插件业务，只回答两件事——
  1. 这个插件跑过哪些版本（幂等：重复安装/重启不会重跑）
  2. 跑过的那版文件有没有被改过（checksum：改旧迁移等于改历史，一律拒绝）

卸载默认保留数据时这张表也跟着保留：它同时是「这个插件曾经有数据」的登记，
管理台的孤儿数据列表就是拿它和 plugin_data/<id>/ 目录派生出来的。
"""
from sqlalchemy import Column, DateTime, Integer, String, UniqueConstraint, func

from app.database import Base


class PluginMigration(Base):
    __tablename__ = "plugin_migrations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    plugin_id = Column(String(80), nullable=False)
    version = Column(String(20), nullable=False, comment="迁移文件名前缀，如 0001")
    checksum = Column(String(64), nullable=False, comment="文件内容的 sha256：改了旧迁移就拒绝")
    filename = Column(String(120), nullable=False, default="")
    applied_at = Column(DateTime, server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("plugin_id", "version", name="uq_plugin_migration_version"),
    )
