"""通道对话额度：每个 QQ 群 / QQ 私聊最多用多少 token

新增 channel_token_policy（每个「AI × 通道种类」的默认口径与额度）与
channel_conversation_usage（每个对话的已用量与可选的单独覆盖）。

动因：QQ 通道接进来的是外部用户，模型的账记在 AI 主人头上——
一次刷屏、一个被转发的群，都可能把主人的 Key 用光而没人看得出来是谁用的。
额度按对话算（群一个、私聊一个），既能统一设默认，也能给单个对话单独调，
用完之后可选自动回一句说明（默认开），而不是静默不回复。

Revision ID: 0096
Revises: 0095
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0096"
down_revision: Union[str, None] = "0095"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "channel_token_policy",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("agent_id", sa.Integer(), nullable=False),
        sa.Column("channel_kind", sa.String(length=20), nullable=False),
        sa.Column("meter_mode", sa.String(length=10), server_default="billable", nullable=False,
                  comment="计费口径：total（全部 token）| billable（未命中输入 + 输出）"),
        sa.Column("limit_tokens", sa.BigInteger(), server_default="1000000", nullable=False,
                  comment="每个对话的默认额度（token）"),
        sa.Column("blocked_reply_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False,
                  comment="超限后自动回一句话（默认开）"),
        sa.Column("blocked_reply_text", sa.Text(), nullable=True,
                  comment="自动回复正文；NULL = 平台默认的官方口吻那一句"),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_id", "channel_kind", name="uq_channel_token_policy"),
    )

    op.create_table(
        "channel_conversation_usage",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("agent_id", sa.Integer(), nullable=False),
        sa.Column("channel_kind", sa.String(length=20), server_default="qq", nullable=False),
        sa.Column("conversation", sa.String(length=120), nullable=False),
        sa.Column("label", sa.String(length=120), server_default="", nullable=False),
        sa.Column("copree_group_id", sa.Integer(), nullable=True),
        sa.Column("dm_session_id", sa.String(length=64), nullable=True),
        sa.Column("limit_override", sa.BigInteger(), nullable=True, comment="单独额度；NULL = 跟随默认"),
        sa.Column("meter_mode_override", sa.String(length=10), nullable=True, comment="单独口径；NULL = 跟随默认"),
        sa.Column("used_total", sa.BigInteger(), server_default="0", nullable=False, comment="按 total 口径累计"),
        sa.Column("used_billable", sa.BigInteger(), server_default="0", nullable=False, comment="按 billable 口径累计"),
        sa.Column("blocked_notice_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_id", "channel_kind", "conversation", name="uq_channel_conversation_usage"),
    )
    op.create_index("ix_channel_conv_usage_group", "channel_conversation_usage",
                    ["agent_id", "copree_group_id"])
    op.create_index("ix_channel_conv_usage_dm", "channel_conversation_usage",
                    ["agent_id", "dm_session_id"])


def downgrade() -> None:
    op.drop_index("ix_channel_conv_usage_dm", table_name="channel_conversation_usage")
    op.drop_index("ix_channel_conv_usage_group", table_name="channel_conversation_usage")
    op.drop_table("channel_conversation_usage")
    op.drop_table("channel_token_policy")
