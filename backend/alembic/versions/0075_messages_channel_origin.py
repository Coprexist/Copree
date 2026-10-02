"""消息记住"来自哪个通道会话"

同一个 Copree 群可能被多个通道实例接着（一个机器人接多个群、多个机器人接同一个群），
出站要回到消息来的那个会话，而"哪条消息来自哪个会话"原先只活在插件进程的内存索引里：
别的实例看不见，于是会退回"本群最近来消息的那条"，把回复发进另一个群。
把会话标识记在消息行上，任何实例、任何时刻都读得到。

Revision ID: 0075
Revises: 0074
Create Date: 2026-10-02
"""
import sqlalchemy as sa
from alembic import op

revision = "0075"
down_revision = "0074"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("messages", sa.Column(
        "channel_origin", sa.String(length=200), nullable=True,
        comment="通道侧的会话标识（QQ 是群/用户 openid）：出站据此回到同一个会话",
    ))


def downgrade() -> None:
    op.drop_column("messages", "channel_origin")
