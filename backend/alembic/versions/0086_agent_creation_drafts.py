"""创建 AI 的草稿表（辅助填写助手的会话）

对话（请求体）不在这张表：它落在 ai_conversation_logs，
conversation_type="creator"、session_id = 本表 id —— 记费用的是同一个口子
（save_conversation_log → usage_daily），与群视界机器人同一条路。

Revision ID: 0086
Revises: 0085
Create Date: 2026-10-05
"""
import sqlalchemy as sa
from alembic import op

revision = "0086"
down_revision = "0085"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_creation_drafts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False, comment="创建者"),
        sa.Column("title", sa.String(length=100), nullable=False, server_default="", comment="草稿名（取首句描述，列表展示用）"),
        sa.Column("form", sa.JSON(), nullable=False, server_default=sa.text("'{}'"), comment="创建表单快照（前端 AgentForm 原样，camelCase）"),
        sa.Column("preset", sa.String(length=30), nullable=True, comment="档位 key（chat|immersive|digital_life）"),
        sa.Column("sub", sa.String(length=40), nullable=True, comment="子档 id"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="open", comment="open=未完成 | created=已建号 | abandoned=已放弃"),
        sa.Column("agent_id", sa.Integer(), nullable=True, comment="建成后的 AI id"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_creation_drafts_user_id", "agent_creation_drafts", ["user_id"])
    op.create_index("ix_agent_creation_drafts_user_status", "agent_creation_drafts", ["user_id", "status", "updated_at"])


def downgrade() -> None:
    op.drop_index("ix_agent_creation_drafts_user_status", table_name="agent_creation_drafts")
    op.drop_index("ix_agent_creation_drafts_user_id", table_name="agent_creation_drafts")
    op.drop_table("agent_creation_drafts")
