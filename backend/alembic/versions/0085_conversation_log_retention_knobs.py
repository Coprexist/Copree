"""对话日志保留策略的旋钮

保留规则改成「按状态老化程度分档」后，各档数值与两个时间阈值都放管理页可配：
活跃档沿用 max_conversation_logs（活跃状态保留数），另外四列是沉寂/老旧档与两个阈值。
一律 nullable、NULL = 用代码默认（默认只留在 utils/pure/conversation_log 一处）。

Revision ID: 0085
Revises: 0084
Create Date: 2026-10-05
"""
import sqlalchemy as sa
from alembic import op

revision = "0085"
down_revision = "0084"
branch_labels = None
depends_on = None

_COLUMNS = (
    ("idle_keep", "沉寂状态留几条（NULL=代码默认）"),
    ("aged_keep", "老旧状态留几条（NULL=代码默认）"),
    ("idle_days", "多少天没动算沉寂（NULL=代码默认）"),
    ("aged_days", "多少天没动算老旧（NULL=代码默认）"),
)


def upgrade() -> None:
    for name, comment in _COLUMNS:
        op.add_column("conversation_log_config", sa.Column(name, sa.Integer(), nullable=True, comment=comment))


def downgrade() -> None:
    for name, _ in _COLUMNS:
        op.drop_column("conversation_log_config", name)