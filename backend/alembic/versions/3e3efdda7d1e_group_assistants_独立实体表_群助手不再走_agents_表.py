"""group_assistants 独立实体表（群助手不再走 agents 表）

Revision ID: 3e3efdda7d1e
Revises: b2c3d4e5f6a7
Create Date: 2026-08-13 14:04:16.657062

群助手 = 独立实体（无账号、无好友、不入群成员表、
不占 agent 体系），与群视界 AI 同形态；绑定群视界群时按类型模板自动创建。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '3e3efdda7d1e'
down_revision: Union[str, None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('group_assistants',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('group_id', sa.Integer(), nullable=False, comment='所属群'),
    sa.Column('world_id', sa.Integer(), nullable=False, comment='所属世界'),
    sa.Column('group_type_slug', sa.String(length=50), nullable=True, comment='类型 slug（定义在 group_types.json）'),
    sa.Column('name', sa.String(length=50), nullable=False, comment='助手名（如 冒险团团长，模板 default_name）'),
    sa.Column('system_prompt', sa.Text(), nullable=True, comment='助手系统提示（模板生成）'),
    sa.Column('model', sa.String(length=50), nullable=True, comment='模型（空 = 世界/默认）'),
    sa.Column('api_key_encrypted', sa.Text(), nullable=True, comment='群主填的自定义 key（Fernet 加密）'),
    sa.Column('api_base_url', sa.Text(), nullable=True, comment='自定义 API 地址'),
    sa.Column('config', postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment='扩展配置'),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
    sa.ForeignKeyConstraint(['group_id'], ['groups.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['world_id'], ['worlds.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_group_assistants_group', 'group_assistants', ['group_id'], unique=False)
    op.create_index('ix_group_assistants_world', 'group_assistants', ['world_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_group_assistants_world', table_name='group_assistants')
    op.drop_index('ix_group_assistants_group', table_name='group_assistants')
    op.drop_table('group_assistants')
