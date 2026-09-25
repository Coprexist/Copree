"""跨状态便签（临时、有时效的跨会话留言）

TODO/PLAN/JOURNAL 是状态内的（工作区），不跨会话。真正需要跨状态传递的是**临时且
有时效**的信息（"群里有人问暗号就答 7788"），所以单开一列，不放工作区。

时效刻度用 agents.llm_call_count：写下后 40 次 API 调用内有效，被别的状态一次性读到
即消费，过期自动丢弃 —— 不需要定时任务清理。

Revision ID: b8d2e4f6a1c3
Revises: f7c1a2b3d4e5
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b8d2e4f6a1c3"
down_revision: Union[str, None] = "f7c1a2b3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "agents",
        sa.Column("cross_state_notes", postgresql.JSONB(astext_type=sa.Text()), nullable=True,
                  comment="跨状态便签：临时/有时效的跨会话留言，40 次 API 调用内有效"),
    )


def downgrade() -> None:
    op.drop_column("agents", "cross_state_notes")
