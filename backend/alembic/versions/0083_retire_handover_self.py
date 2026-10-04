"""帧后事由谁办

帧全量保留之后，"超容量的帧走的时候留不留记录"成了一个档位取向：
数字生命档的 AI 自己接手后事（挂起待交接、办完调 finish_frame 销帧），
其余档位由平台直接代销（删记录 + 留一条「平台代销」告知）。
存量一律按"平台代销"起步——那是改造前的行为，不静默改变已有 AI 的语义。

Revision ID: 0083
Revises: 0082
Create Date: 2026-10-04
"""
import sqlalchemy as sa
from alembic import op

revision = "0083"
down_revision = "0082"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agents", sa.Column(
        "retire_handover_self", sa.Boolean(), nullable=True, server_default=sa.false(),
        comment="帧后事自己交接（关=平台代销）",
    ))


def downgrade() -> None:
    op.drop_column("agents", "retire_handover_self")
