"""通道互动召回记账表

互动召回（is_wakeup）是平台给的主动触达能力：对方主动对话后 30 天内 4 个周期各 1 条。
额度记在内存里重启就归零，会把用过的周期再用一次而被腾讯拒发，故单开一张表。
一行 = 一个通道实例对一个目标的周期起点 + 已用周期位。

Revision ID: 0074
Revises: 0073
Create Date: 2026-10-01
"""
import sqlalchemy as sa
from alembic import op

revision = "0074"
down_revision = "0073"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "channel_wakeup_ledger",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("kind", sa.String(length=80), nullable=False, comment="通道/来源种类，如 qq-channel"),
        sa.Column("owner_scope", sa.String(length=120), nullable=False, server_default="",
                  comment="该通道下的归属实例：QQ 是 agent-<agentId>"),
        sa.Column("target", sa.String(length=200), nullable=False, comment="通道侧的对话对象：QQ 单聊是用户 openid"),
        sa.Column("anchor_at", sa.DateTime(), nullable=False, comment="本周期起点：对方最近一次主动对话"),
        sa.Column("used_mask", sa.Integer(), nullable=False, server_default="0",
                  comment="已用掉的周期位掩码，bit i = 第 i 个周期已经发过一条"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("kind", "owner_scope", "target", name="uq_channel_wakeup"),
    )


def downgrade() -> None:
    op.drop_table("channel_wakeup_ledger")
