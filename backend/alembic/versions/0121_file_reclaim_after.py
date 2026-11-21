"""未发送附件的回收时刻（步 6）

「上传了但没发」的附件以前没人管：落在 data/attachments/ 里、永远没人引用，用户也看不见
（输入框一关就没了引用）。现在它住在 users/{uid}/chat/ 里，靠这一列定死回收时刻（§3.5）。

- reclaim_after：NULL＝不回收（已发出、属于消息的附件，或在我的文件里）；
  有值＝到这个时刻连字节带行一起删；
- 上传时算 now() + 24h，发出时清空（track_attachments 那条 UPDATE），
  所以「发出去」这件事只有一个写入口，不会出现时钟没停的情况；
- 存量回填给 7 天宽限：迁移前传的那些文件，用户当时并不知道有这条规则，
  直接按 created_at + 24h 算等于上线即静默清理（§11 步 5.1 的提醒）。

Revision ID: 0121
Revises: 0120
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0121"
down_revision: Union[str, None] = "0120"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "file_metadata",
        sa.Column("reclaim_after", sa.DateTime(), nullable=True,
                  comment="回收时刻；NULL=不回收（已发出或在我的文件里）"),
    )
    # 存量：只在「我在对话里产生的」那一栏、没被消息带走过、字节还在的行上开计时。
    # 已经发出去的行 archived_to_message_at 非空，天然被排除；我的文件在 users/{uid}/files。
    op.execute("""
        UPDATE file_metadata
        SET reclaim_after = now() + interval '7 days'
        WHERE path LIKE 'users/%/chat/%'
          AND archived_to_message_at IS NULL
          AND physical_key IS NOT NULL
    """)


def downgrade() -> None:
    op.drop_column("file_metadata", "reclaim_after")
