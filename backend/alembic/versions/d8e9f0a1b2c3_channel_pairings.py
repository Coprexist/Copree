"""通道配对表：陌生私聊先领配对码，主人批准后放行

QQ 官方只给按机器人加密的 openid（拿不到 QQ 号），所以"谁在跟 AI 说话"只能靠 openid 认；
配对表就是这份名单：pending（领了码等批）/ approved（放行）/ blocked（拉黑）。

Revision ID: d8e9f0a1b2c3
Revises: b7d1e2f3a4c5
Create Date: 2026-09-25

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'd8e9f0a1b2c3'
down_revision: Union[str, None] = 'b7d1e2f3a4c5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'channel_pairings',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('plugin_id', sa.String(length=80), nullable=False, comment='插件 id，如 qq-channel'),
        sa.Column('instance', sa.String(length=40), nullable=False, server_default='',
                  comment='实例 id（QQ 通道是 agent-<agentId>）'),
        sa.Column('openid', sa.String(length=160), nullable=False, comment='通道侧用户标识（QQ 为 openid）'),
        sa.Column('nickname', sa.String(length=120), nullable=True, comment='通道侧昵称，只用于展示'),
        sa.Column('code', sa.String(length=12), nullable=True, comment='配对码：用户在 QQ 里收到，回平台核对'),
        sa.Column('status', sa.String(length=16), nullable=True, comment='pending | approved | blocked'),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('approved_at', sa.DateTime(), nullable=True),
        sa.UniqueConstraint('plugin_id', 'instance', 'openid', name='uq_channel_pairing'),
    )
    op.create_index('ix_channel_pairings_lookup', 'channel_pairings', ['plugin_id', 'instance', 'status'])


def downgrade() -> None:
    op.drop_index('ix_channel_pairings_lookup', table_name='channel_pairings')
    op.drop_table('channel_pairings')
