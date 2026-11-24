"""AI 设置项：文字直发方式 + 工具集范围

两列都是**AI 级设置项**，不是渠道开关：档位（chat/immersive/digital_life）只是
它们的预设组合，用户在详细设置里随时可以单独覆盖——这是「档位 = 自由组合」的落点。

  plain_text_action  silent（默认，现状：不发出、写账本并提醒）
                      send（直接发到当前状态帧对应的会话）
  tool_scope          all（默认）/ minimal（精简核心集）/ none（不给工具）

Revision ID: 0129
Revises: 0128
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0129"
down_revision: Union[str, None] = "0128"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("agents")}
    if "plain_text_action" not in cols:
        op.add_column("agents", sa.Column(
            "plain_text_action", sa.String(16), nullable=False, server_default="silent",
            comment="模型只回文字时：silent=不发出 / send=直接发到当前会话"))
    if "tool_scope" not in cols:
        op.add_column("agents", sa.Column(
            "tool_scope", sa.String(16), nullable=False, server_default="all",
            comment="给模型多少工具：all / minimal / none"))


def downgrade() -> None:
    op.drop_column("agents", "tool_scope")
    op.drop_column("agents", "plain_text_action")