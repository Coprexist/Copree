"""工具调用日聚合（分帧类型与在线状态）

新增 tool_usage_daily：按「日期 × 工具 × 帧类型 × 在线状态」累计调用次数。
动因：现有 metrics_collector 的计数在进程内存里，重启即失且没有维度，
拿不到「聊天帧里到底用哪几个工具」这类一手信息；而下一步「哪些段常驻、哪些折叠」
必须靠这份数据决定，不能拍脑袋。

形态对齐 usage_daily：日聚合 + 唯一键 + 只增不删。

Revision ID: 0093
Revises: 0092
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0093"
down_revision: Union[str, None] = "0092"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 锁定态的工具名单：只冻定义版本不够，名单是每轮按状态现算的（见 capability_versioning）
    op.add_column(
        "agents",
        sa.Column("cap_effective_allowed", sa.JSON(), nullable=True,
                  comment="锁定态工具名单（{状态}|allowed → [工具名]，compact 时清空重算）"),
    )
    op.create_table(
        "tool_usage_daily",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("stat_date", sa.Date(), nullable=False, comment="统计日期（UTC）"),
        sa.Column("tool_name", sa.String(length=80), nullable=False, comment="工具名"),
        sa.Column("frame_type", sa.String(length=40), server_default="", nullable=False,
                  comment="帧类型；空串 = 无帧/未标注"),
        sa.Column("agent_state", sa.String(length=20), server_default="", nullable=False,
                  comment="active/dnd/inactive/blocked；空串 = 未知"),
        sa.Column("calls", sa.Integer(), server_default="0", nullable=False, comment="调用次数"),
        sa.Column("errors", sa.Integer(), server_default="0", nullable=False, comment="失败次数"),
        sa.Column("latency_ms_sum", sa.BigInteger(), server_default="0", nullable=False,
                  comment="耗时累加（ms）；平均 = sum / calls"),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stat_date", "tool_name", "frame_type", "agent_state",
                            name="uq_tool_usage_daily_key"),
    )


def downgrade() -> None:
    op.drop_table("tool_usage_daily")
    op.drop_column("agents", "cap_effective_allowed")
