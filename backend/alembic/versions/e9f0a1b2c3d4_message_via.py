"""消息入口标识：群里要能看出这条是从 QQ 来的

- messages.via：NULL=站内，qq=QQ 通道。只用于界面画"来源"标识，不影响投递与唤醒
- 与 source_public_id 的分工：那个是联邦对端实例（更细的来源），这个是"从哪条通道进来的"

Revision ID: e9f0a1b2c3d4
Revises: d8e9f0a1b2c3
Create Date: 2026-09-25

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'e9f0a1b2c3d4'
down_revision: Union[str, None] = 'd8e9f0a1b2c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('messages', sa.Column(
        'via', sa.String(length=16), nullable=True,
        comment='消息入口通道：NULL=站内；qq=QQ 通道（联邦来源另见 source_public_id）',
    ))


def downgrade() -> None:
    op.drop_column('messages', 'via')
