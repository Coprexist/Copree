"""状态帧容量

帧从"用完即丢"改成"全量保留、只在他交接完后事之后才删"，于是需要一个容量闸：
存储帧数超过它，就把最久没被激活的帧挂起待交接。默认 31，AI 可自配。

Revision ID: 0082
Revises: 0081
Create Date: 2026-10-04
"""
import sqlalchemy as sa
from alembic import op

revision = "0082"
down_revision = "0081"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agents", sa.Column(
        "frame_capacity", sa.Integer(), nullable=True,
        comment="状态帧容量（NULL=默认 31）",
    ))


def downgrade() -> None:
    op.drop_column("agents", "frame_capacity")
