"""agents.foci：焦段定义（会话轴 / 语义轴）

记忆的适用范围存成 agent 级的一份定义，预置的「所有聊天」由纯函数兜底注入，
不写进存量数据——加一列即可，旧数据读出来就是只有预置焦段。

Revision ID: 0070
Revises: 0069
Create Date: 2026-09-26
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0070"
down_revision = "0069"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agents", sa.Column(
        "foci", sa.JSON().with_variant(JSONB, "postgresql"), nullable=True))


def downgrade() -> None:
    op.drop_column("agents", "foci")
