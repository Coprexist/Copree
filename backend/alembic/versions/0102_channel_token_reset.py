"""channel_token 自动重置：策略加周期/锚点，用量行加窗口起点

为什么用「窗口」而不是定时任务清账：窗口起点由 (周期, 锚点, 当前时间) 算出来，
读的时候发现窗口变了就当用量是 0，写的时候顺手把计数归零。这样不需要 cron、
重启不丢账、改设置下一次读就生效，也不用存"上次重置时间"。

window_start 用 UTC naive（与全站时间列一致）；reset_anchor 的 HH:MM 按平台
display_timezone 解释——用户在前端看到的就是它。

Revision ID: 0102
Revises: 0101
"""
from alembic import op
import sqlalchemy as sa


revision = "0102"
down_revision = "0101"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("channel_token_policy",
                  sa.Column("reset_period", sa.String(length=10), nullable=False, server_default=""))
    op.add_column("channel_token_policy",
                  sa.Column("reset_anchor", sa.String(length=5), nullable=False, server_default="00:00"))
    op.add_column("channel_conversation_usage",
                  sa.Column("window_start", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("channel_conversation_usage", "window_start")
    op.drop_column("channel_token_policy", "reset_anchor")
    op.drop_column("channel_token_policy", "reset_period")
