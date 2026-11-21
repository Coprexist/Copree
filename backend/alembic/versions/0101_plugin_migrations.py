"""plugin_migrations：插件自带迁移的记账表

插件可以「申请自己的表」：在插件目录写 migrations/NNNN_*.py，平台按序执行并在这里
记一行；文件被改过（checksum 不符）就拒绝启动——改历史迁移会让老库和新库分叉。

卸载默认保留数据，所以这张表的行也保留：它同时是「这个插件曾有数据」的登记。

Revision ID: 0101
Revises: 0100
"""
from alembic import op
import sqlalchemy as sa


revision = "0101"
down_revision = "0100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "plugin_migrations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("plugin_id", sa.String(length=80), nullable=False),
        sa.Column("version", sa.String(length=20), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("filename", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("applied_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("plugin_id", "version", name="uq_plugin_migration_version"),
    )


def downgrade() -> None:
    op.drop_table("plugin_migrations")
