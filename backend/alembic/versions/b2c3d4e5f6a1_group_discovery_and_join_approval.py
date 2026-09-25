"""群发现与入群审批：groups 三开关 + 入群申请表 + 邀请审批字段

- groups.searchable / auto_approve_join / approve_invites 默认 false / true / false，
  即**保持改动前的行为**（搜不到、直接进、邀请免审），老群不受影响。
- group_invitations.approval_status：成员邀请需审批时先卡审批，群主/管理员批准后才通知
  被邀请人；存量记录 server_default='approved' 不会被历史数据拦下。
- group_join_requests：自动审批关闭时的入群申请，与好友申请一起进「申请列表」。

Revision ID: b2c3d4e5f6a1
Revises: a1b2c3d4e5f0
Create Date: 2026-09-21 20:10:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a1'
down_revision: Union[str, None] = 'a1b2c3d4e5f0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('groups', sa.Column('searchable', sa.Boolean(), nullable=False, server_default=sa.text('false')))
    op.add_column('groups', sa.Column('auto_approve_join', sa.Boolean(), nullable=False, server_default=sa.text('true')))
    op.add_column('groups', sa.Column('approve_invites', sa.Boolean(), nullable=False, server_default=sa.text('false')))

    op.add_column('group_invitations', sa.Column('approval_status', sa.String(20), nullable=False, server_default='approved'))
    op.add_column('group_invitations', sa.Column('approved_by', sa.Integer(), nullable=True))
    op.add_column('group_invitations', sa.Column('approved_at', sa.DateTime(), nullable=True))

    op.create_table(
        'group_join_requests',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('group_id', sa.Integer(), sa.ForeignKey('groups.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('status', sa.String(20), nullable=False, server_default='pending'),
        sa.Column('message', sa.Text(), nullable=True),
        sa.Column('resolver_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('resolved_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_group_join_requests_group_status', 'group_join_requests', ['group_id', 'status'])
    op.create_index('ix_group_join_requests_user_status', 'group_join_requests', ['user_id', 'status'])


def downgrade() -> None:
    op.drop_index('ix_group_join_requests_user_status', table_name='group_join_requests')
    op.drop_index('ix_group_join_requests_group_status', table_name='group_join_requests')
    op.drop_table('group_join_requests')
    op.drop_column('group_invitations', 'approved_at')
    op.drop_column('group_invitations', 'approved_by')
    op.drop_column('group_invitations', 'approval_status')
    op.drop_column('groups', 'approve_invites')
    op.drop_column('groups', 'auto_approve_join')
    op.drop_column('groups', 'searchable')
