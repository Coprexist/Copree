"""外部通道影子账号标记：users.origin_channel

QQ 等通道来的用户是插件新建的影子账号，没有 API Key 也没有额度。
账单人规则（executor._get_api_config）要能识别它们，才好在"外部通道来的会话"上
把账单记到 AI 主人头上——否则四层链解析为空，AI 只能发系统通知、不会回复。

Revision ID: f0a1b2c3d4e5
Revises: e9f0a1b2c3d4
Create Date: 2026-09-25

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'f0a1b2c3d4e5'
down_revision: Union[str, None] = 'e9f0a1b2c3d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column(
        'origin_channel', sa.String(length=16), nullable=True,
        comment='外部通道影子账号来源：qq=QQ 通道；NULL=站内注册',
    ))
    # 已有的通道影子账号也补上标记（按锚点邮箱识别），否则它们仍然解析不到账单人
    op.execute("UPDATE users SET origin_channel = 'qq' WHERE email LIKE '%@qq.bridge'")


def downgrade() -> None:
    op.drop_column('users', 'origin_channel')
