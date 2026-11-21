"""账本 seq 的发放计数：每个会话一行

为什么要有这张表：账本条目原来是 `SELECT MAX(seq)+1` 分配序号。两个写者同时写同一段会话时，
谁也看不见对方**还没提交**的行，于是算出同一个号；后到的 INSERT 撞唯一索引、原地等先到的
提交。先到的那笔如果正等着后到的返回（回合里调插件工具就是这个形状），两边互等、没有超时——
2026-11-07 群 81 因此冻结 12 小时，整条 AI 回复队列跟着停。

现在号由数据库一次领走（`history_service._reserve`：INSERT ... ON CONFLICT DO UPDATE
... RETURNING），领到的区间只属于自己。首次写入时按账本现有的 MAX(seq) 自播种，
所以这里**不需要回填**：老库升级后，第一次 append 会自动接上原来的序号。

Revision ID: 0107
Revises: 0106
Create Date: 2026-11-07
"""
from alembic import op
import sqlalchemy as sa
from typing import Sequence, Union

revision: str = '0107'
down_revision: Union[str, None] = '0106'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'agent_history_seq',
        sa.Column('agent_id', sa.Integer(), nullable=False),
        sa.Column('context_ref', sa.String(length=64), nullable=False,
                  comment='会话标识：group:{id} / dm:{session}'),
        sa.Column('last_seq', sa.Integer(), nullable=False,
                  comment='已经发到第几号（下一批从这里往后领）'),
        sa.ForeignKeyConstraint(['agent_id'], ['agents.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('agent_id', 'context_ref'),
        comment='账本 seq 的发放计数（每个会话一行）',
    )


def downgrade() -> None:
    op.drop_table('agent_history_seq')
