"""把过低的工具轮次抬到下限 6

一轮要装得下「搜索 → 回复 → 进页面核实 → 有出入再补一条」，被动回复低于 6 轮会在核实
与更正之间被截断；闹钟唤醒的自主任务同样不该低于这个下限。只抬低于下限的值，
用户自己调高的不动。

Revision ID: 0072
Revises: 0071
Create Date: 2026-09-26
"""
from alembic import op

revision = "0072"
down_revision = "0071"
branch_labels = None
depends_on = None

FLOOR = 6


def upgrade() -> None:
    op.execute(f"UPDATE agents SET max_tool_rounds = {FLOOR} WHERE max_tool_rounds < {FLOOR}")
    op.execute(f"UPDATE agents SET alarm_max_tool_rounds = {FLOOR} WHERE alarm_max_tool_rounds < {FLOOR}")


def downgrade() -> None:
    # 只抬不降：旧值没有留档，回滚也无从还原
    pass
