"""合作者表情包权限：主人决定合作者能否改这个 AI 能用哪些表情包

新增 agents.collaborators_manage_emoji（默认关）。

表情包与通道是两件事：包是创建者（用户）的资产，而这个开关决定"这个 AI 能拿什么发图"，
属于它对外表现的授权面。默认只有主人能动；需要合作者代配时由主人打开。

Revision ID: 0119
Revises: 0118
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0119"
down_revision: Union[str, None] = "0118"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "agents",
        sa.Column("collaborators_manage_emoji", sa.Boolean(), nullable=False,
                  server_default=sa.text("false"),
                  comment="是否允许合作者改此 AI 的表情包开关（默认否）"),
    )


def downgrade() -> None:
    op.drop_column("agents", "collaborators_manage_emoji")
