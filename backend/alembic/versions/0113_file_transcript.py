"""file_metadata.transcript：语音说了什么

从消息的 attachments 快照搬到文件本身（唯一来源）：文稿是会变的数据
（AI 语音由 text 直接给、人的语音等 ASR、重转还会改），塞进 N 条消息的快照
就会出现 N 份不一致；而它本来是**这份音频**的属性。

存量回填：把此前写在消息快照里的 transcript 抄回来（只在 Postgres 上做；
SQLite 部署里语音本就没几条，由应用侧懒补）。
"""
from alembic import op
import sqlalchemy as sa

revision = "0113"
down_revision = "0112"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("file_metadata", sa.Column(
        "transcript", sa.Text(), nullable=True,
        comment="语音说了什么：AI 语音 = 它念的那段 text，人的语音 = ASR 结果",
    ))

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # 只回填真正存在过的：attachments[*].transcript
        bind.execute(sa.text("""
            UPDATE file_metadata fm
               SET transcript = src.body
              FROM (
                    SELECT (att->>'file_id')::int AS file_id,
                           max(att->>'transcript')  AS body
                      FROM messages m
                      CROSS JOIN LATERAL jsonb_array_elements(
                          CASE WHEN jsonb_typeof(m.attachments) = 'array'
                               THEN m.attachments ELSE '[]'::jsonb END
                      ) AS att
                     WHERE att ? 'transcript'
                     GROUP BY (att->>'file_id')::int
                   ) AS src
             WHERE fm.id = src.file_id
               AND fm.transcript IS NULL
        """))


def downgrade() -> None:
    op.drop_column("file_metadata", "transcript")
