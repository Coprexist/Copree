"""群名可以跟随外部通道

一个 AI 接进两个 QQ 群时，两个落点群默认都叫「<AI 名> 的群」——界面和 AI 都只能靠 id 分辨。
把"群名跟着通道那边的群名走"做成群设置里的开关：打开＝这个名字由通道维护（通道侧群名变了就对齐）。

存量处理：只把"还顶着自动名、又确实被通道接着"的群置为开——那本来就是通道建的兜底名；
其余一律保持关，不替用户决定他的群叫什么。

Revision ID: 0079
Revises: 0078
Create Date: 2026-10-04
"""
import sqlalchemy as sa
from alembic import op

revision = "0079"
down_revision = "0078"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("groups", sa.Column(
        "name_from_channel", sa.Boolean(), nullable=False, server_default=sa.text("false"),
        comment="群名由外部通道维护（跟随通道侧群名）",
    ))
    op.execute("""
        UPDATE groups g SET name_from_channel = true
        WHERE EXISTS (
            SELECT 1 FROM group_members gm JOIN agents a ON a.user_id = gm.member_id
            WHERE gm.group_id = g.id AND gm.member_type = 'ai' AND g.name = a.name || ' 的群'
        )
        AND EXISTS (
            SELECT 1 FROM plugin_configs pc
            WHERE (pc.key = 'copree_group_id' AND pc.value = g.id::text)
               OR (pc.key = 'group_map' AND pc.value LIKE '{%'
                   AND EXISTS (SELECT 1 FROM jsonb_each_text(pc.value::jsonb) e WHERE e.value = g.id::text))
        )
    """)


def downgrade() -> None:
    op.drop_column("groups", "name_from_channel")
