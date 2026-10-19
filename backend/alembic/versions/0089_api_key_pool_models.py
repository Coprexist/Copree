"""api_key_pool 记下自己能用哪些模型

池 Key 原来只记地址（api_base_url）和关联供应商（provider_name），"待选模型"要么去供应商预设里
猜，要么就用 AI 自己的 chat_model——2026-10-19 的 400 就是这么来的：AI 选了 xAI 的 grok-4.6，
凭证解析却落到 DeepSeek 的池 Key 上，每轮都被拒。

Revision ID: 0089
Revises: 0088
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# 与模型层的 json_column() 同一口径：PG 上是 JSONB，别的方言退化成 JSON
JSON_TYPE = sa.JSON().with_variant(JSONB, "postgresql")

revision = "0089"
down_revision = "0088"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("api_key_pool", sa.Column("models", JSON_TYPE, nullable=True,
                                            comment="待选模型 id 列表；NULL=按关联供应商预设"))
    op.add_column("api_key_pool", sa.Column("default_model", sa.String(length=100), nullable=True,
                                            comment="通道不认 AI 自选模型时用它"))


def downgrade() -> None:
    op.drop_column("api_key_pool", "default_model")
    op.drop_column("api_key_pool", "models")
