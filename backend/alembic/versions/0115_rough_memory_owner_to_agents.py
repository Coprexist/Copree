"""把 rough_memories 里按虚拟账号写下的 owner_id 归正成 agents.id

记忆是 **AI 独有**的东西（见 docs/dev/id_semantics.md），owner_id 一律记 agents.id。
这条规矩被一条遗留的 HTTP 端点破坏过：它让人类（或 AI 的虚拟账号）直接往 rough_memories
写记忆，owner_type 仍标 'ai'、owner_id 用的是 users.id。同一张表出现两种 id 口径，
编号撞上就会串门（agent 3 ↔ user 3、agent 25 ↔ user 25 都是实测撞号）。

那份端点已在同一批改动里删掉（全仓没有调用方，只有历史规格书提过）。这里只归正存量：
**仅当 owner_id 不是任何 agents.id、但恰好等于某个 agent 的虚拟账号时**才改写——
已经是 agents.id 的行（281 行）一个字节都不动。
"""
from alembic import op
import sqlalchemy as sa

revision = "0115"
down_revision = "0114"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    bind.execute(sa.text("""
        UPDATE rough_memories r
           SET owner_id = a.id
          FROM agents a
         WHERE r.owner_type = 'ai'
           AND a.user_id = r.owner_id
           AND NOT EXISTS (SELECT 1 FROM agents x WHERE x.id = r.owner_id)
    """))


def downgrade() -> None:
    # 归正是单向的：把 agents.id 再变回虚拟账号只会制造同一类混乱
    pass
