"""对话日志记下「这一轮用的是哪条配置」

api_config_chain.md 批次一的「日志补列」一直没做：日志只有 model，看不出这轮走的是
AI 绑定 / 池 / 用户默认哪一档、哪条配置、哪个端点——排障时只能靠猜。

补一列 api_meta 存 {source, label, model, base_url}，写入方是请求时的同一个
Resolution（与 stepper 同源），这样「日志里看到的」和「实际打的」不会各说各话。

Revision ID: 0134
Revises: 0133
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# 与模型层的 json_column() 同一口径：PG 上是 JSONB，别的方言退化成 JSON
JSON_TYPE = sa.JSON().with_variant(JSONB, "postgresql")

revision: str = "0134"
down_revision: Union[str, None] = "0133"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ai_conversation_logs",
        sa.Column("api_meta", JSON_TYPE, nullable=True,
                  comment="本轮实际使用的配置：{source, label, model, base_url}"),
    )


def downgrade() -> None:
    op.drop_column("ai_conversation_logs", "api_meta")
