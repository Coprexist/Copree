"""对话日志的 conversation_type 加宽：world_event / friend_request 一直存不进去

列宽原为 String(10)，group / dm / alarm 那会儿够用。之后世界事件（world_event，11 字符）
与好友申请（friend_request，13 字符）也走这张表：Postgres 对 varchar(10) 超长直接报错，
错误只被打进日志（"保存对话日志失败"），于是 AI 的世界事件对话一轮账本都没留下。

Revision ID: 0098
Revises: 0097
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0098"
down_revision: Union[str, None] = "0097"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "ai_conversation_logs",
        "conversation_type",
        existing_type=sa.String(length=10),
        type_=sa.String(length=32),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "ai_conversation_logs",
        "conversation_type",
        existing_type=sa.String(length=32),
        type_=sa.String(length=10),
        existing_nullable=False,
    )
