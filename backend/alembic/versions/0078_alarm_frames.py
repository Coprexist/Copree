"""计划与闹钟绑状态帧

计划到点是在某个状态里执行的（唤醒时构建的就是那个状态的请求体），所以闹钟要记住"在哪个帧里执行"；
还要记住它是在哪段会话里被排出来的——帧 id 会随 pop/重建变，会话轴不会，计划板的归属与"排给别处"的
渲染都靠它。

Revision ID: 0078
Revises: 0077
Create Date: 2026-10-03
"""
import sqlalchemy as sa
from alembic import op

revision = "0078"
down_revision = "0077"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_alarms", sa.Column(
        "frame_id", sa.String(length=12), nullable=True,
        comment="唤醒时恢复的目标状态帧（帧活在 agents.state_stack 的 JSON 里，不是外键）",
    ))
    op.add_column("agent_alarms", sa.Column(
        "origin_context_ref", sa.String(length=64), nullable=True,
        comment="排这条计划的会话；帧 id 会重建，会话轴不会",
    ))
    # 存量 AI 一律关：打开会改变它们每轮的提示词与花费，升级不该顺手涨价
    op.add_column("agents", sa.Column(
        "plan_injection_enabled", sa.Boolean(), nullable=False,
        server_default=sa.text("false"), comment="把计划与闹钟投进上下文",
    ))


def downgrade() -> None:
    op.drop_column("agents", "plan_injection_enabled")
    op.drop_column("agent_alarms", "origin_context_ref")
    op.drop_column("agent_alarms", "frame_id")
