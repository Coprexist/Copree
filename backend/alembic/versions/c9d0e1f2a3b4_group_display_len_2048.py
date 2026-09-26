"""群聊单条消息展示上限 256 → 2048（折叠改成前 75% + 后 25%）

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-09-26
"""
from alembic import op

revision = "c9d0e1f2a3b4"
down_revision = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 只动"还是旧默认 256"的群：手动改过的值（0=不截断、或别的数字）保持原样
    op.execute("UPDATE groups SET max_msg_display_len = 2048 WHERE max_msg_display_len = 256")
    op.execute("ALTER TABLE groups ALTER COLUMN max_msg_display_len SET DEFAULT 2048")


def downgrade() -> None:
    op.execute("UPDATE groups SET max_msg_display_len = 256 WHERE max_msg_display_len = 2048")
    op.execute("ALTER TABLE groups ALTER COLUMN max_msg_display_len SET DEFAULT 256")
