"""插件协议 v3：服务类插件（category=service）、多实例与插件级配置

- plugin_configs：插件级配置，机密项存 Fernet 密文；instance 让"一个插件多份配置"
  （每个 QQ 机器人一份）成立——实例是同一个插件的配置，不是另一个插件
- plugin_service_states：服务插件的期望运行状态，按 (plugin_id, instance) 存，
  重启后据此恢复（否则管理员停掉的实例会在下次重启时自己跑起来）。
  不给 plugins 加外键：内置服务（browser）没有 plugins 行

Revision ID: b7d1e2f3a4c5
Revises: c3d4e5f6a7b3
Create Date: 2026-09-25

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b7d1e2f3a4c5'
down_revision: Union[str, None] = 'c3d4e5f6a7b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'plugin_configs',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('plugin_id', sa.String(length=80), nullable=False),
        sa.Column('instance', sa.String(length=40), nullable=False, server_default='',
                  comment='实例 id（多实例插件用，如机器人别名）；单实例为空串'),
        sa.Column('key', sa.String(length=80), nullable=False, comment='配置键（与 config_schema 同名）'),
        sa.Column('value', sa.Text(), nullable=True, comment='明文值；is_secret 时存 Fernet 密文'),
        sa.Column('is_secret', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['plugin_id'], ['plugins.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('plugin_id', 'instance', 'key', name='uq_plugin_config_key'),
    )
    op.create_index('ix_plugin_configs_plugin_id', 'plugin_configs', ['plugin_id'])

    op.create_table(
        'plugin_service_states',
        sa.Column('plugin_id', sa.String(length=80), primary_key=True),
        sa.Column('instance', sa.String(length=40), primary_key=True, server_default=''),
        sa.Column('desired_running', sa.Boolean(), nullable=False, server_default=sa.true(),
                  comment='期望运行状态：重启后据此恢复'),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table('plugin_service_states')
    op.drop_index('ix_plugin_configs_plugin_id', table_name='plugin_configs')
    op.drop_table('plugin_configs')
