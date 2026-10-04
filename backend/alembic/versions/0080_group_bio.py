"""群简介

群一直只有公告（说事的地方），没有「一句话介绍这个群」的地方——资料卡上那句「空空如也便是此群的简介」
无处可填。补一个字段：群主/管理员在群设置里写，资料卡显示（没写就退回通道侧的群简介）。

Revision ID: 0080
Revises: 0079
Create Date: 2026-10-04
"""
import sqlalchemy as sa
from alembic import op

revision = "0080"
down_revision = "0079"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("groups", sa.Column(
        "bio", sa.String(length=300), nullable=True, comment="群简介（一句话介绍这个群）",
    ))


def downgrade() -> None:
    op.drop_column("groups", "bio")
