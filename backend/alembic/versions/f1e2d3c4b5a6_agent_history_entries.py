"""会话历史账本：agent_history_entries（2026-09-25 设计，见 docs/dev/conversation_history.md）

会话上下文改成"两卷历史 + 只追加"：账本条目存**渲染好的最终字节**，段内只 append，
只在 compact / 超时压缩（解锁点）重写。这样每轮重拼字节一致 → 前缀缓存命中，
且一次性通知（能力变更、便签撤下）落库后不会再"说完就没了"。

Revision ID: f1e2d3c4b5a6
Revises: b8d2e4f6a1c3
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f1e2d3c4b5a6"
down_revision: Union[str, None] = "b8d2e4f6a1c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_history_entries",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("agent_id", sa.Integer(), nullable=False),
        sa.Column("context_ref", sa.String(length=64), nullable=False,
                  comment="会话标识：group:{id} / dm:{session}"),
        sa.Column("seq", sa.Integer(), nullable=False, comment="同一会话内的单调序号，唯一排序依据"),
        sa.Column("kind", sa.String(length=16), nullable=False,
                  comment="message/gap/backfill/tool/note/notice/suggestion/summary/thinking"),
        sa.Column("actor", sa.String(length=16), nullable=False, server_default="system",
                  comment="self=我 / user=用户 / world=外界 / system=平台"),
        sa.Column("content", sa.Text(), nullable=False, comment="渲染好的最终字节（渲染即落库）"),
        sa.Column("ref", sa.String(length=128), nullable=True,
                  comment="来源锚点：message_id / tool_call_id"),
        sa.Column("flags", postgresql.JSONB(astext_type=sa.Text()), nullable=True,
                  comment="可压/已撤下等标记（只由解锁点改写）"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_id", "context_ref", "seq", name="uq_agent_history_seq"),
    )
    op.create_index("ix_agent_history_ctx_seq", "agent_history_entries",
                    ["agent_id", "context_ref", "seq"])


def downgrade() -> None:
    op.drop_index("ix_agent_history_ctx_seq", table_name="agent_history_entries")
    op.drop_table("agent_history_entries")
