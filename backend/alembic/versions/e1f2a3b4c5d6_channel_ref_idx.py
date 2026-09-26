"""出站精准引用：messages / dm_messages 加通道引用索引（QQ 的 REFIDX）

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
Create Date: 2026-09-26
"""
import sqlalchemy as sa
from alembic import op

revision = "e1f2a3b4c5d6"
down_revision = "d0e1f2a3b4c5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("messages", "dm_messages"):
        op.add_column(table, sa.Column("channel_ref_idx", sa.Text(), nullable=True))


def downgrade() -> None:
    for table in ("messages", "dm_messages"):
        op.drop_column(table, "channel_ref_idx")
