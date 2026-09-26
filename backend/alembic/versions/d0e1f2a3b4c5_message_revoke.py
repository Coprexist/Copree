"""消息撤回：messages / dm_messages 加撤回标记与通道消息 id

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-09-26
"""
import sqlalchemy as sa
from alembic import op

revision = "d0e1f2a3b4c5"
down_revision = "c9d0e1f2a3b4"
branch_labels = None
depends_on = None

_COLUMNS = (
    # 撤回：时间 + 谁撤的（users.id，审计用）
    sa.Column("revoked_at", sa.DateTime(), nullable=True),
    sa.Column("revoked_by", sa.Integer(), nullable=True),
    # 通道侧那条消息的 id（QQ 撤回要它）：NULL = 没经通道 / 通道没回 id
    sa.Column("channel_msg_id", sa.Text(), nullable=True),
)


def upgrade() -> None:
    for table in ("messages", "dm_messages"):
        for column in _COLUMNS:
            op.add_column(table, column.copy())


def downgrade() -> None:
    for table in ("messages", "dm_messages"):
        for column in _COLUMNS:
            op.drop_column(table, column.name)
