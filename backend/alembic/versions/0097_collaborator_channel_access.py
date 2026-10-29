"""合作者通道权限：主人决定合作者能否看到并编辑此 AI 的通道配置

新增 agents.collaborators_manage_channels（默认关）。
通道配置里是机器人凭据（AppID / ClientSecret / NapCat access_token），
默认只有主人能碰；需要合作者代管时由主人在合作者区域打开这个开关。

Revision ID: 0097
Revises: 0096
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0097"
down_revision: Union[str, None] = "0096"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "agents",
        sa.Column("collaborators_manage_channels", sa.Boolean(), nullable=False,
                  server_default=sa.text("false"),
                  comment="是否允许合作者查看并编辑此 AI 的通道配置（默认否）"),
    )


def downgrade() -> None:
    op.drop_column("agents", "collaborators_manage_channels")
