"""AI 级表情包开关：平台装好一堆资源，用不用由每个 AI 的创建者决定

默认白名单：装了包不等于所有 AI 都能发——创建者在自己 AI 的设置里勾上才算。
一条 (agent_id, pack_id) 一行；enabled=false 也记一行（黑名单模式下用来整体关掉某个包，
比"删行 = 回到默认"更难读懂，所以显式记下用户的决定）。
"""
from alembic import op
import sqlalchemy as sa

revision = "0111"
down_revision = "0110"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_emoji_packs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("agent_id", sa.Integer(), sa.ForeignKey("agents.id", ondelete="CASCADE"),
                  nullable=False, comment="AI 本体 id（agents.id），不是虚拟账号 users.id"),
        sa.Column("pack_id", sa.String(length=80), nullable=False, comment="表情包插件 id"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true(),
                  comment="这个 AI 用不用这个包"),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), onupdate=sa.func.now()),
        sa.UniqueConstraint("agent_id", "pack_id", name="uq_agent_emoji_pack"),
        sa.Index("ix_agent_emoji_packs_agent", "agent_id"),
    )


def downgrade() -> None:
    op.drop_table("agent_emoji_packs")
