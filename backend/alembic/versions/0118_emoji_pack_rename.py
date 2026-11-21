"""内置表情包改名：emoji-qq → emoji

表情包不再叫「QQ 表情」，也不再依赖 QQ——QQ 那边的名字/face 解析归 QQ 通道插件，
站内这套只认「名字 ↔ 一张图」。磁盘上的插件 id 跟着改了，这里把库里按旧 id 记下的
两处数据挪过去：

- `plugins` 行：旧 id 的行先删掉，新行由 sync_plugins_to_db 按磁盘重建（留着会变成两份）
- `agent_emoji_packs.pack_id`：每个 AI 的开关决定，不能丢

同一个 AI 若同时有新旧两条显式开关，先去重再改名，否则撞 (agent_id, pack_id) 唯一约束。
"""
from alembic import op

revision = "0118"
down_revision = "0117"
branch_labels = None
depends_on = None

OLD = "emoji-qq"
NEW = "emoji"


def upgrade() -> None:
    op.execute(
        f"DELETE FROM agent_emoji_packs WHERE pack_id = '{OLD}' AND agent_id IN ("
        f"SELECT agent_id FROM agent_emoji_packs WHERE pack_id = '{NEW}')"
    )
    op.execute(f"UPDATE agent_emoji_packs SET pack_id = '{NEW}' WHERE pack_id = '{OLD}'")
    op.execute(f"DELETE FROM plugins WHERE id = '{OLD}'")


def downgrade() -> None:
    op.execute(f"DELETE FROM agent_emoji_packs WHERE pack_id = '{NEW}'")
    op.execute(f"UPDATE agent_emoji_packs SET pack_id = '{OLD}' WHERE pack_id = '{NEW}'")
    op.execute(f"DELETE FROM plugins WHERE id = '{NEW}'")
