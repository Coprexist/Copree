"""user_api_providers 记住「system 摆放这个结论是怎么来的」

0123 给这张表加了 system_layout，但它只有一个值，没有来源：

  - 用户手选的 anywhere
  - 探出来的 leading
  - 预设给的 leading
  - 从来没判过、默认填的 anywhere

四者在库里长得一模一样。于是运行时无法判断「这一档能不能信」：用户自己填了本机
llama-server，行里躺着默认的 anywhere，每轮把 system 塞进对话中段，Qwen 的 Jinja
直接 500（System message must be at the beginning）。

加三列与管理员 provider_config 条目同构（那边一直有这三个字段）：

  system_layout_source       user | detected | preset | host；NULL = 从未判定
  system_template_hash       探测到的模板指纹（换了模型能看出来）
  system_layout_checked_at   最后一次判定的时间

NULL 表示「没有判定证据」：运行时对这类行按地址保守取值（字面私网 → leading），
用户下次保存这条厂商时会被真实判定覆盖。

Revision ID: 0126
Revises: 0125
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0126"
down_revision: Union[str, None] = "0125"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("user_api_providers", sa.Column(
        "system_layout_source", sa.String(16), nullable=True,
        comment="system_layout 的来源：user|detected|preset|host；NULL = 从未判定",
    ))
    op.add_column("user_api_providers", sa.Column(
        "system_template_hash", sa.String(32), nullable=True,
        comment="探测到的 chat 模板指纹：判断同一台机器换模型了没有",
    ))
    op.add_column("user_api_providers", sa.Column(
        "system_layout_checked_at", sa.DateTime(), nullable=True,
        comment="最后一次判定 system_layout 的时间",
    ))


def downgrade() -> None:
    op.drop_column("user_api_providers", "system_layout_checked_at")
    op.drop_column("user_api_providers", "system_template_hash")
    op.drop_column("user_api_providers", "system_layout_source")