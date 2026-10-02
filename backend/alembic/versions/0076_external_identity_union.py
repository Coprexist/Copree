"""外部身份：跨应用 union 与本地锚点

同一个人在 QQ 的每个机器人眼里各是一个 openid（官方只有 union_openid 是"跨应用统一"，
且可能为空），于是站内把他存成了好几行、好几个账号、各一份记忆。
这里补两列：union_id 记跨应用标识，user_id 记这个地址对应的本地锚点账号——
同一 union 的多个地址共用同一个锚点，@ 翻译也从"反解邮箱"改成直接读这一列。

Revision ID: 0076
Revises: 0075
Create Date: 2026-10-02
"""
import sqlalchemy as sa
from alembic import op

revision = "0076"
down_revision = "0075"
branch_labels = None
depends_on = None

# 历史行没有 union（过去没存过），但锚点账号可以从邮箱拼法反推：{origin}@{kind}.bridge
_BACKFILL_SQL = """
UPDATE external_identities
SET user_id = (
    SELECT u.id FROM users u
    WHERE u.email = external_identities.origin || '@' || external_identities.kind || '.bridge'
)
WHERE user_id IS NULL
"""


def upgrade() -> None:
    op.add_column("external_identities", sa.Column(
        "union_id", sa.String(length=200), nullable=True,
        comment="跨应用统一标识（QQ 是 union_openid）",
    ))
    op.add_column("external_identities", sa.Column(
        "user_id", sa.Integer(), nullable=True,
        comment="这个通道地址对应的本地锚点账号",
    ))
    op.create_foreign_key(
        "fk_external_identity_user", "external_identities", "users",
        ["user_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index("ix_external_identity_union", "external_identities", ["kind", "union_id"])
    if op.get_bind().dialect.name == "postgresql":
        op.execute(_BACKFILL_SQL)


def downgrade() -> None:
    op.drop_index("ix_external_identity_union", table_name="external_identities")
    op.drop_constraint("fk_external_identity_user", "external_identities", type_="foreignkey")
    op.drop_column("external_identities", "user_id")
    op.drop_column("external_identities", "union_id")
