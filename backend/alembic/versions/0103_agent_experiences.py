"""agent_experiences：跨情景经历索引

为什么单开一张表，而不是复用记忆或结构记忆：
- 向量记忆是「问起才想起」——相关性召回、会衰减，答不了「我在别处干过什么」；
- 结构记忆是 AI 自己选择记的，平台没法保证它写过；
- 经历索引要的是「平台兜底写一行、账本投递、进过就不再投」，读写口径都与前两者不同。

归属用 `owner_type + owner_id`（与 rough_memories 同一套约定）：主站 AI 与世界 AI 不共用
id 空间，`ai` 与 `world_ai` 两类实体都落这一张表，读写路径也只此一套。

写入侧：轮末 key_note（AI 自述这段干了什么）、场景收口时的工具总账、AI 的 note_experience。
读取侧：账本条目的 ref = `exp:<id>`，做过投递去重；不带内容指纹（经历行写成后不改）。

Revision ID: 0103
Revises: 0102
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# 与模型层的 json_column() 同一口径：PG 上是 JSONB，别的方言退化成 JSON
JSON_TYPE = sa.JSON().with_variant(JSONB, "postgresql")


revision = "0103"
down_revision = "0102"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_experiences",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("owner_type", sa.String(length=20), nullable=False, server_default="ai",
                  comment="ai | world_ai"),
        sa.Column("owner_id", sa.Integer(), nullable=False,
                  comment="agents.id / world_ais.id"),
        sa.Column("scene_key", sa.String(length=64), nullable=False, server_default="",
                  comment="场景键（会话 / 帧的 context_ref）"),
        sa.Column("scene_label", sa.String(length=100), nullable=False, server_default="",
                  comment="场景名（群名 / 私信对方 / 世界会话）"),
        sa.Column("frame_type", sa.String(length=20), nullable=False, server_default="",
                  comment="group_chat | dm | 插件帧类型 ..."),
        sa.Column("source", sa.String(length=20), nullable=False, server_default="key_note",
                  comment="key_note | tool_ledger | ai"),
        sa.Column("summary", sa.Text(), nullable=False, comment="一行：场景 + 干了什么"),
        sa.Column("tools", sa.Text(), nullable=False, server_default="",
                  comment="当轮工具总账（可选）"),
        sa.Column("refs", JSON_TYPE, nullable=True, comment="关联：消息 id 范围 / 帧 id"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_agent_experiences_scene", "agent_experiences",
                    ["owner_type", "owner_id", "scene_key", "id"])
    op.create_index("ix_agent_experiences_recent", "agent_experiences",
                    ["owner_type", "owner_id", "id"])


def downgrade() -> None:
    op.drop_index("ix_agent_experiences_recent", table_name="agent_experiences")
    op.drop_index("ix_agent_experiences_scene", table_name="agent_experiences")
    op.drop_table("agent_experiences")
