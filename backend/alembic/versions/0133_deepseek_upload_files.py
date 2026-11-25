"""DeepSeek Files API 上传索引表

图片不再每轮以 base64 内联（48 MiB 请求体上限 + 每轮重传浪费配额），改为：
上传一次拿 `file_id`，请求里用 `{"type":"file","file_id":...}` 引用。
远端 file 会过期/被删，且同一张图会被多个会话复用——这层状态必须有地方存，
不能塞进 messages.attachments 的 JSON 快照（那个写一次就不再变）。

作用域 `scope = sha256(base_url | api_key)`：不同 key 一律不共享（无法判断是否同一账号），
同一 key 内按内容 sha256 去重；一行 = 一个引用方（agent + context_ref），
remote_file_id 在多行间复用，最后一行消失时才删远端。

Revision ID: 0133
Revises: 0132
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0133"
down_revision: Union[str, None] = "0132"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if "deepseek_upload_files" in set(sa.inspect(bind).get_table_names()):
        return
    op.create_table(
        "deepseek_upload_files",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("scope", sa.String(64), nullable=False),
        sa.Column("agent_id", sa.Integer(), nullable=False),
        sa.Column("context_ref", sa.String(128), nullable=False),
        sa.Column("local_file_id", sa.Integer(), nullable=True),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("remote_file_id", sa.String(128), nullable=False),
        sa.Column("mime_type", sa.String(100), nullable=True),
        sa.Column("bytes", sa.BigInteger(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("scope", "content_sha256", "agent_id", "context_ref",
                            name="uq_deepseek_upload_ref"),
    )
    op.create_index("ix_deepseek_upload_scope_content", "deepseek_upload_files",
                    ["scope", "content_sha256"])
    op.create_index("ix_deepseek_upload_scope_created", "deepseek_upload_files",
                    ["scope", "created_at"])


def downgrade() -> None:
    op.drop_table("deepseek_upload_files")
