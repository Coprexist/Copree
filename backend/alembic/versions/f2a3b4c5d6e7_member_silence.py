"""按人静音（只对某个人：连 @ 也不唤醒）

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-09-26
"""
import sqlalchemy as sa
from alembic import op

revision = "f2a3b4c5d6e7"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 按人静音：until_at 与 remaining_count 各自可空（都空 = 永久）
    op.create_table(
        "member_silences",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("agent_id", sa.Integer(),
                  sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("group_id", sa.Integer(),
                  sa.ForeignKey("groups.id", ondelete="CASCADE"), nullable=False),
        sa.Column("target_user_id", sa.Integer(), nullable=False),
        sa.Column("until_at", sa.DateTime(), nullable=True),
        sa.Column("remaining_count", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint("agent_id", "group_id", "target_user_id", name="uq_member_silence"),
    )
    op.create_index("ix_member_silences_lookup", "member_silences",
                    ["agent_id", "group_id", "target_user_id"])


def downgrade() -> None:
    op.drop_index("ix_member_silences_lookup", table_name="member_silences")
    op.drop_table("member_silences")
