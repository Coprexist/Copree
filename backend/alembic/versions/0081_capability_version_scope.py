"""能力版本作用域

变更通知只发一次的病根：告知进度（known）挂在 agent 级，谁先构建提示词谁把版本推平，
别的状态就再也收不到那条变更——记忆索引正文又冻结在 effective 快照上，于是那个状态里
"Ai 自己写下的规则"连文本都不存在。补一列作用域：决定这条变更该通知哪些状态。
空 / '*' = 全部状态（平台、提示词、记忆索引这类内容每个状态都装同一份）；
也可以是某个会话键或 focus:{id}。历史行留空即按"全部"处理，不需要回填。

Revision ID: 0081
Revises: 0080
Create Date: 2026-10-04
"""
import sqlalchemy as sa
from alembic import op

revision = "0081"
down_revision = "0080"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("capability_versions", sa.Column(
        "scope", sa.String(length=64), nullable=True,
        comment="变更作用域：空或 *=全部状态",
    ))


def downgrade() -> None:
    op.drop_column("capability_versions", "scope")
