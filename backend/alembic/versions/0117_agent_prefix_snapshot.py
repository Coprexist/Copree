"""agents.cap_effective_prefix：锁定态前缀文本快照

为什么锁"输出"而不是只锁"输入"：现有机制锁的是版本号与工具名单（cap_effective_versions /
cap_effective_allowed），但前缀里的技能目录、表情包清单、记忆索引都是**渲染出来的文字**，
渲染代码里任何一处现算（新增工具行、加一个表情包、状态文案）都会改字节，而版本号没变、
锁形同虚设。2026-11-14 实测：一天里 send_voice / file_fetch / 大肥鱼表情包三次改动，
活跃会话的前缀从第 0 字节失效 7 次，多付 283k 未命中。

所以把**渲染结果**整段存下来：首见钉住，之后每轮直接用这份字节，解锁点（compact/clear）
清空重算。键是会话键——前缀按会话拼，一个会话 compact 不该换别的会话的字节。

Revision ID: 0117
Revises: 0116
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0117"
down_revision = "0116"
branch_labels = None
depends_on = None

# 与模型层的 json_column() 同一口径：PG 上是 JSONB，别的方言（桌面版 SQLite）退化成 JSON
JSON_TYPE = sa.JSON().with_variant(JSONB, "postgresql")


def upgrade() -> None:
    op.add_column("agents", sa.Column("cap_effective_prefix", JSON_TYPE, nullable=True))


def downgrade() -> None:
    op.drop_column("agents", "cap_effective_prefix")
