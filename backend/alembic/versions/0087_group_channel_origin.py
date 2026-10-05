"""groups 加两列：origin_channel（通道建的落点群）+ archived_at（合并走的源群）

Revision ID: 0087
Revises: 0086
"""
from alembic import op
import sqlalchemy as sa

revision = "0087"
down_revision = "0086"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("groups", sa.Column("origin_channel", sa.String(length=20), nullable=True,
                                      comment="由哪条通道建出来当落点的（如 qq）"))
    op.add_column("groups", sa.Column("archived_at", sa.DateTime(), nullable=True,
                                      comment="归档时刻（合并走的源群）"))


def downgrade() -> None:
    op.drop_column("groups", "archived_at")
    op.drop_column("groups", "origin_channel")

