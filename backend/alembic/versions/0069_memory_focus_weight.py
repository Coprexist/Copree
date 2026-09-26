"""记忆的焦段锚点与权值：rough_memories + structured_records

两套记忆都带上：
- mem_type / value_score：设定权值 1-5（时间权值不落库，读取时现算）；
- session_refs / session_foci / semantic_foci：焦段锚点，均以 JSON 存，三组各自可空；
- last_touched_at / last_touched_call：时间权值的基准（最近一次被召回）。

rough_memories.value_score 原注释按 1-10 使用，实际写入只有 1 和 5；
此处把越界值夹回 1-5 完成量纲归一（用 CASE 写法，SQLite 没有 GREATEST/LEAST）。

Revision ID: 0069
Revises: f2a3b4c5d6e7
Create Date: 2026-09-26
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0069"
down_revision = "f2a3b4c5d6e7"
branch_labels = None
depends_on = None


def _anchors() -> list[sa.Column]:
    """两套记忆共用的锚点与时间基准列（语义见 utils/pure/memory_weight.py）。"""
    json_type = sa.JSON().with_variant(JSONB, "postgresql")
    return [
        sa.Column("mem_type", sa.String(20), server_default="daily", nullable=False),
        sa.Column("session_refs", json_type, nullable=True),
        sa.Column("session_foci", json_type, nullable=True),
        sa.Column("semantic_foci", json_type, nullable=True),
        sa.Column("last_touched_at", sa.DateTime(), nullable=True),
        sa.Column("last_touched_call", sa.Integer(), nullable=True),
    ]


def upgrade() -> None:
    # 向量记忆：value_score 已存在，只补类型、锚点与时间基准
    for column in _anchors():
        op.add_column("rough_memories", column)
    op.execute("UPDATE rough_memories SET value_score = 5 WHERE value_score > 5")
    op.execute("UPDATE rough_memories SET value_score = 1 WHERE value_score < 1")

    # 结构记忆：原本没有权值，一并补齐（默认「一般」）
    op.add_column("structured_records",
                  sa.Column("value_score", sa.Integer(), server_default="3", nullable=False))
    for column in _anchors():
        op.add_column("structured_records", column)


def downgrade() -> None:
    for name in ("last_touched_call", "last_touched_at", "semantic_foci",
                 "session_foci", "session_refs", "mem_type"):
        op.drop_column("structured_records", name)
    op.drop_column("structured_records", "value_score")
    for name in ("last_touched_call", "last_touched_at", "semantic_foci",
                 "session_foci", "session_refs", "mem_type"):
        op.drop_column("rough_memories", name)
