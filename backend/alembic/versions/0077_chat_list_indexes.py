"""聊天列表与未读聚合的索引

侧栏刷新、未读计数、每条新消息都要跑 /groups 与 /dm/sessions，这些查询全部落在
(group_id, created_at) / (session_id, ...) 上，而 messages、dm_messages 过去只有主键——
等于每次都全表扫（实测 25 个群的一轮列表 177 条 SQL，每条都在扫同一张表）。
「我加入了哪些群」「我参与的私信」也各缺一条按人过滤的索引。

Revision ID: 0077
Revises: 0076
Create Date: 2026-10-03
"""
from alembic import op

revision = "0077"
down_revision = "0076"
branch_labels = None
depends_on = None

# 现在的表还小，直接建是瞬间的事；哪天真的大到要 CREATE INDEX CONCURRENTLY，
# 得单独开一条 autocommit 的迁移（迁移默认在事务里，CONCURRENTLY 进不去）
_INDEXES = [
    ("ix_messages_group_created", "ON messages (group_id, created_at)"),
    ("ix_group_members_member", "ON group_members (member_id, member_type)"),
    ("ix_dm_sessions_user2", "ON dm_sessions (user2_id)"),
    ("ix_dm_messages_session_created", "ON dm_messages (session_id, created_at)"),
    # 未读只占少数：部分索引把已读的行排除在外，未读计数与「标记已读」都走它
    ("ix_dm_messages_unread", "ON dm_messages (session_id) WHERE read_at IS NULL"),
]


def upgrade() -> None:
    for name, tail in _INDEXES:
        op.execute(f"CREATE INDEX IF NOT EXISTS {name} {tail}")


def downgrade() -> None:
    for name, _tail in reversed(_INDEXES):
        op.execute(f"DROP INDEX IF EXISTS {name}")
