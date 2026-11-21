"""清掉「AI 发自己的文件」被误记的转发引用

修的是判据：文件 owner_id 记 agents.id，而消息 sender_id 是虚拟账号 users.id，
旧代码直接比数字，于是 AI 发自己的文件也会落一条 forward，把自己的配额算掉一份
（2026-11-14 真机：file 214 是 ai:120 自己的语音，发送时却记了 forward ai:120）。

判据已在 file_service.is_file_owner 里收敛（两种写法都认），这里只清存量：
删掉「文件属于这个 AI、且引用方正是它的虚拟账号」的 forward 行。别的都不动——
AI 转发别人的文件、人类转发，本来就是要留痕的。
"""
from alembic import op
import sqlalchemy as sa

revision = "0114"
down_revision = "0113"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # SQLite 部署里语音本就没几条，且这是纯清污：跳过比写两套 SQL 划算
        return
    bind.execute(sa.text("""
        DELETE FROM file_references fr
         USING file_metadata fm, agents a
         WHERE fr.file_id = fm.id
           AND fr.ref_type = 'forward'
           AND fr.referrer_type = 'ai'
           AND fm.owner_type = 'ai'
           AND a.id = fm.owner_id
           AND a.user_id = fr.referrer_id
    """))


def downgrade() -> None:
    # 误记的数据不重建：重建没有意义，也不该在回滚时把错误算回去
    pass
