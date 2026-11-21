"""给 reclaim_after 加索引（步 7：撤回回收）

回收器从「一小时扫一次」改成「每分钟扫一次」：撤回的附件定的是 1 分钟后排删（§5.6），
一小时才扫一次就等于延迟一小时。没有索引，那一分钟一次就是一次全表扫。

Revision ID: 0122
Revises: 0121
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0122"
down_revision: Union[str, None] = "0121"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_file_metadata_reclaim_after", "file_metadata", ["reclaim_after"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_file_metadata_reclaim_after", table_name="file_metadata")
