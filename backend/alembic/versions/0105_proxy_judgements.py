"""代理 LLM 判定留痕

Revision ID: 0105
Revises: 0104
Create Date: 2026-11-02

- 新增 proxy_judgements：一行一次「要不要拉起本尊」的判断。
  94% 停用线、影子期的正确拦截/误杀统计都读它；幂等键是 (agent_id, message_id)。
"""
from alembic import op
import sqlalchemy as sa
from typing import Sequence, Union

revision: str = '0105'
down_revision: Union[str, None] = '0104'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'proxy_judgements',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('agent_id', sa.Integer(),
                  sa.ForeignKey('agents.id', ondelete='CASCADE'), nullable=False),
        sa.Column('frame_type', sa.String(length=40), nullable=False, server_default=''),
        sa.Column('context_ref', sa.String(length=120), nullable=False, server_default=''),
        sa.Column('message_id', sa.BigInteger(), nullable=True),
        sa.Column('wake', sa.Boolean(), nullable=False),
        sa.Column('reason', sa.String(length=200), nullable=False, server_default=''),
        sa.Column('urgency', sa.String(length=10), nullable=False, server_default='normal'),
        sa.Column('fallback', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('model', sa.String(length=80), nullable=False, server_default=''),
        sa.Column('latency_ms', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint('agent_id', 'message_id', name='uq_proxy_judgement_message'),
    )
    op.create_index('ix_proxy_judgement_agent_created', 'proxy_judgements',
                    ['agent_id', 'created_at'])


def downgrade() -> None:
    op.drop_index('ix_proxy_judgement_agent_created', table_name='proxy_judgements')
    op.drop_table('proxy_judgements')
