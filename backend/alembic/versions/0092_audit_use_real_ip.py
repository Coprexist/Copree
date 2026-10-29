"""system_settings：审计记录真实客户端 IP 的开关

Revision ID: 0092
Revises: 0091
"""
from alembic import op
import sqlalchemy as sa

revision = "0092"
down_revision = "0091"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "system_settings",
        sa.Column(
            "audit_use_real_ip",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=True,
            comment="审计是否记录访客真实 IP（需可信代理透传 X-Real-IP），默认关闭",
        ),
    )


def downgrade() -> None:
    op.drop_column("system_settings", "audit_use_real_ip")
