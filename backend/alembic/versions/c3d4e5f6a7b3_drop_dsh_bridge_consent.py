"""撤销 Copree 侧的 DSH 接入同意（闸门只留 DSH 侧）

上一版把「同意接入」做成了要在 Copree 管理端点一次，方向弄反了：真正的闸门在 DSH
（设置 → Copree 点同意后插件才开始发心跳），Coproee 侧那道只保护 Copree 自己，而
Coproee 侧本来就只有管理员能发指令——同一个管理员点两次不是两道锁。

所以 dsh_bridge_config 这一列（同意清单）整体撤掉；表结构回到上一版。

Revision ID: c3d4e5f6a7b3
Revises: c3d4e5f6a7b2
Create Date: 2026-09-24 23:10:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c3d4e5f6a7b3'
down_revision: Union[str, None] = 'c3d4e5f6a7b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column('system_settings', 'dsh_bridge_config')


def downgrade() -> None:
    from sqlalchemy.dialects import postgresql

    op.add_column(
        'system_settings',
        sa.Column('dsh_bridge_config', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
