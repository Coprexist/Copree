"""groups.concurrent_ai_limit 的默认值改成 2

Revision ID: 0088
Revises: 0087
"""
from alembic import op
import sqlalchemy as sa

revision = "0088"
down_revision = "0087"
branch_labels = None
depends_on = None

NEW_DEFAULT = 2
OLD_DEFAULT = 3


def upgrade() -> None:
    op.alter_column("groups", "concurrent_ai_limit", server_default=sa.text(str(NEW_DEFAULT)))
    op.alter_column(
        "system_settings", "default_concurrent_ai_limit", server_default=sa.text(str(NEW_DEFAULT))
    )
    # 老数据：群设置面板以前根本没把这个字段写进库（它不在更新 schema 里），所以库里还是
    # 老默认值的那些不可能是谁选的——一起归到新默认，免得面板上看着像被人改过。
    op.execute(f"UPDATE groups SET concurrent_ai_limit = {NEW_DEFAULT} "
               f"WHERE concurrent_ai_limit = {OLD_DEFAULT}")


def downgrade() -> None:
    op.alter_column("groups", "concurrent_ai_limit", server_default=sa.text(str(OLD_DEFAULT)))
    op.alter_column(
        "system_settings", "default_concurrent_ai_limit", server_default=sa.text(str(OLD_DEFAULT))
    )
