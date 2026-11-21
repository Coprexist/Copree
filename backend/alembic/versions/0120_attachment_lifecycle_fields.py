"""附件生命周期：可改性、归档时间、最近引用、物理键

四个字段服务同一件事——「这个文件现在归谁、还算不算在用、清掉能不能真的省盘」：

- mutable：内容可不可变（与 collaboration_mode 的「谁能看」拆开）
- archived_to_message_at：被消息带走的时间；非空＝已归档，不再出现在「我的文件」
- last_referenced_at：最近一次被消息引用；清理梯度按它排新旧，不按 created_at
- physical_key：硬链接组标识；用量按它去重，删字节前按它查同组有没有活行

回填口径：last_referenced_at / archived_to_message_at 取「引用它的消息的 created_at」
（群消息 + 私信，撤回的不算）。physical_key **不在迁移里回填**——Postgres 看不到 inode，
存量由 scripts/backfill_physical_key.py 用 stat 补；不补也是对的，计量侧把 NULL 当「自己一组」，
只是没享受到硬链接去重。

设计：docs/dev/message_storage_and_cleanup.md §3.2、§5.2、§3.3、§5.7

Revision ID: 0120
Revises: 0119
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0120"
down_revision: Union[str, None] = "0119"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "file_metadata",
        sa.Column("mutable", sa.Boolean(), nullable=False, server_default=sa.text("true"),
                  comment="内容可不可变（与 collaboration_mode 的可见性分开）"),
    )
    op.add_column(
        "file_metadata",
        sa.Column("archived_to_message_at", sa.DateTime(), nullable=True,
                  comment="被消息带走的时间；非空＝已归档，不再出现在我的文件"),
    )
    op.add_column(
        "file_metadata",
        sa.Column("last_referenced_at", sa.DateTime(), nullable=True,
                  comment="最近一次被消息引用；清理梯度按它排新旧"),
    )
    op.add_column(
        "file_metadata",
        sa.Column("physical_key", sa.String(64), nullable=True,
                  comment="硬链接组标识 i:<dev>:<ino> 或 f:<源行id>；用量去重与删字节判活"),
    )

    # 回填：先算「每个文件被哪些消息引用过」，群消息与私信都算（都是"发出去了"）。
    # 撤回的消息不算——撤回来的附件要回到清理队列（§5.6）。
    op.execute("""
        CREATE TEMP TABLE _ref_backfill AS
        SELECT file_id, MAX(created_at) AS max_at, MIN(created_at) AS first_at
        FROM (
            SELECT (a->>'file_id')::int AS file_id, m.created_at
            FROM (
                SELECT created_at, attachments AS jb FROM messages
                WHERE attachments IS NOT NULL
                  AND jsonb_typeof(attachments) = 'array'
                  AND revoked_at IS NULL
            ) m CROSS JOIN LATERAL jsonb_array_elements(m.jb) AS a
            WHERE a ? 'file_id' AND (a->>'file_id') ~ '^[0-9]+$'
            UNION ALL
            SELECT (a->>'file_id')::int, m.created_at
            FROM (
                SELECT created_at, attachments::jsonb AS jb FROM dm_messages
                WHERE attachments IS NOT NULL AND attachments <> ''
                  AND jsonb_typeof(attachments::jsonb) = 'array'
            ) m CROSS JOIN LATERAL jsonb_array_elements(m.jb) AS a
            WHERE a ? 'file_id' AND (a->>'file_id') ~ '^[0-9]+$'
        ) t
        GROUP BY file_id
    """)
    op.execute("""
        UPDATE file_metadata f
        SET last_referenced_at = r.max_at,
            archived_to_message_at = r.first_at
        FROM _ref_backfill r
        WHERE f.id = r.file_id
    """)
    # 从没被引用过的文件：它的"最近引用"就是它的创建时间
    op.execute("UPDATE file_metadata SET last_referenced_at = created_at WHERE last_referenced_at IS NULL")
    op.execute("DROP TABLE _ref_backfill")

    # 回填完才挂默认值：先挂的话 ADD COLUMN 会把存量行一律填成"此刻"，
    # 再想区分"从没被引用过"就只能靠猜。默认值本身给新上传的行用（上传即"最近"）
    op.alter_column("file_metadata", "last_referenced_at", nullable=False,
                    server_default=sa.func.now())

    op.create_index("idx_file_metadata_owner_physical", "file_metadata",
                    ["owner_type", "owner_id", "physical_key"])
    op.create_index("idx_file_metadata_archive_ref", "file_metadata",
                    ["archived_to_message_at", "last_referenced_at"])
    op.create_index("idx_file_metadata_physical", "file_metadata", ["physical_key"])


def downgrade() -> None:
    op.drop_index("idx_file_metadata_physical", table_name="file_metadata")
    op.drop_index("idx_file_metadata_archive_ref", table_name="file_metadata")
    op.drop_index("idx_file_metadata_owner_physical", table_name="file_metadata")
    op.drop_column("file_metadata", "physical_key")
    op.drop_column("file_metadata", "last_referenced_at")
    op.drop_column("file_metadata", "archived_to_message_at")
    op.drop_column("file_metadata", "mutable")
