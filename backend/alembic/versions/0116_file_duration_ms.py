"""file_metadata.duration_ms：音频时长（不可变属性）

为什么单开一列而不是塞进消息的 attachments 快照：时长是**文件**的属性，
同一个音频被引用 N 次就存 N 份，改起来永远对不齐（与 0113 的 transcript 同一个理由）。
区别在于 transcript 会变（先没有、转写完才有），所以只存这一处、读时按 id 取；
时长不会变，所以**同时**允许随快照走一份——会话列表要它，为此每条会话再查一次库不值。

Revision ID: 0116
Revises: 0115
"""
from alembic import op
import sqlalchemy as sa

revision = "0116"
down_revision = "0115"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("file_metadata", sa.Column("duration_ms", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("file_metadata", "duration_ms")
