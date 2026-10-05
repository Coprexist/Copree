"""对话日志按状态分桶保留

日志从「每 AI 留最近 N 条」改成「按状态分桶」：日志最多的那段状态留 30（可配）、其余各 5；
超 30 天没动的状态再收一档（10 / 2）。分桶要靠状态身份，于是在写入时算好存一列
（state_key = 帧身份 type|label），裁剪不必回读 messages（那列是整份请求体）。

存量回填走与写入、列表同一个函数（state_frame_of + state_key_of），口径不另起一份。

Revision ID: 0084
Revises: 0083
Create Date: 2026-10-05
"""
import json

import sqlalchemy as sa
from alembic import op

revision = "0084"
down_revision = "0083"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ai_conversation_logs", sa.Column(
        "state_key", sa.String(128), nullable=True,
        comment="那轮的状态帧身份 type|label（裁剪按它分桶）",
    ))
    op.create_index("ix_ai_conversation_logs_state_key", "ai_conversation_logs", ["state_key"])

    # 存量回填（一次性；行数 = 每 AI 几十条）
    from app.utils.pure.state_stack import state_frame_of, state_key_of

    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, messages FROM ai_conversation_logs")).fetchall()
    for row in rows:
        messages = row.messages
        if isinstance(messages, str):
            try:
                messages = json.loads(messages)
            except ValueError:
                messages = []
        key = state_key_of(state_frame_of(messages or []))
        if key:
            conn.execute(
                sa.text("UPDATE ai_conversation_logs SET state_key = :key WHERE id = :id"),
                {"key": key, "id": row.id},
            )


def downgrade() -> None:
    op.drop_index("ix_ai_conversation_logs_state_key", table_name="ai_conversation_logs")
    op.drop_column("ai_conversation_logs", "state_key")