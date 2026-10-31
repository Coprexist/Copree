"""message_channel_refs：通道标识按实例存（同一句话每台机器人各一份）

为什么：一个 Copree 群能被好几台机器人接着，同一个 QQ 群在每台机器人那里的
group_openid / msg_id / REFIDX 都是各自一套。合并成一条站内消息后只留一份通道 id，
就会出现"拿 A 的索引去 B 的群里引用/撤回"——2026-10-31 真机 40034138「引用消息越权」，
整条消息发不出去。

迁移只建表，不回填：老数据的单列 channel_msg_id / channel_ref_idx 保留，
读的时候按"新表没有就退回老列（且必须同会话）"处理，不需要一次性写回。

Revision ID: 0099
Revises: 0098
"""
from alembic import op
import sqlalchemy as sa

revision = "0099"
down_revision = "0098"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "message_channel_refs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("message_id", sa.Integer(),
                  sa.ForeignKey("messages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("instance", sa.String(length=64), nullable=False),
        sa.Column("conversation", sa.String(length=200), nullable=False),
        sa.Column("channel_msg_id", sa.Text(), nullable=True),
        sa.Column("channel_ref_idx", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint("message_id", "instance", "conversation",
                            name="uq_message_channel_ref"),
    )
    op.create_index("ix_message_channel_refs_message", "message_channel_refs", ["message_id"])


def downgrade() -> None:
    op.drop_index("ix_message_channel_refs_message", table_name="message_channel_refs")
    op.drop_table("message_channel_refs")
