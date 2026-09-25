"""DSH 桥接同意清单：system_settings 增加 dsh_bridge_config

「Coproee 再同意」这道闸门要跨重启存活，所以把管理员的决定落在单行表上：

- approved: {instanceId: {advertiseUrl, version, approvedAt, approvedBy}} —— 已同意接入的 DSH
- denied:   [instanceId] —— 明确拒绝过的（重新同意需要管理员再点一次）

存量库：新列为 NULL，读侧按「没同意过」处理（默认拒绝，符合安全默认）。

Revision ID: c3d4e5f6a7b2
Revises: b2c3d4e5f6a1
Create Date: 2026-09-24 22:10:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'c3d4e5f6a7b2'
down_revision: Union[str, None] = 'b2c3d4e5f6a1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'system_settings',
        sa.Column(
            'dsh_bridge_config',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment='DSH 桥接同意清单（已同意/已拒绝的实例）',
        ),
    )


def downgrade() -> None:
    op.drop_column('system_settings', 'dsh_bridge_config')
