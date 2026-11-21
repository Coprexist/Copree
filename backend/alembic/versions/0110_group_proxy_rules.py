"""群级决策代理规矩与公告可见性

代理 LLM 判定「要不要拉起本尊」时，上下文里除了场景与 AI 自己的性格，还该有「这个群的标准」。
规矩写在群上而不是 AI 帧上：同一个群里所有居民 AI 共用一份，群主/管理员改一次就生效。

公告可能只想给人看，所以单独给一个开关：关掉后代理上下文里不带本群公告，判定照常进行，
只是少一段依据（不关判定、也不影响 Human 侧看到公告）。

Revision ID: 0110
Revises: 0109
Create Date: 2026-11-11
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0110'
down_revision: Union[str, None] = '0109'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('groups', sa.Column(
        'proxy_rules', sa.Text(), nullable=True,
        comment='决策代理在本群的规矩（群主/管理员写）'))
    op.add_column('groups', sa.Column(
        'proxy_announcement', sa.Boolean(), nullable=False,
        server_default=sa.text('true'), comment='是否把本群公告给决策代理看'))


def downgrade() -> None:
    op.drop_column('groups', 'proxy_announcement')
    op.drop_column('groups', 'proxy_rules')
