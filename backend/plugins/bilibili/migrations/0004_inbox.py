"""收件箱游标：B 站那边的回复 / @ / 私信，念到哪一条了。

为什么不直接清 B 站的未读标记：那是**用户手机上**的红点。替他清掉，等于把人的消息
在手机上也标成读过了。所以自己记游标——(agent_id, kind) 一行，kind = reply / at / im:<talker_id>，
cursor 是 B 站给的那条消息的 id / msg_seqno（都是递增的）。
"""
import sqlalchemy as sa


def upgrade(conn, t) -> None:
    meta = sa.MetaData()

    sa.Table(
        t("inbox"), meta,
        sa.Column("agent_id", sa.Integer, nullable=False, comment="这个 AI 的收件箱"),
        sa.Column("kind", sa.String(48), nullable=False,
                  comment="reply / at / im:<talker_id>"),
        sa.Column("cursor", sa.BigInteger, nullable=False, server_default="0",
                  comment="已经注入过的那条消息 id / seqno"),
        sa.Column("updated_at", sa.BigInteger, nullable=False, server_default="0",
                  comment="epoch 秒"),
        sa.PrimaryKeyConstraint("agent_id", "kind", name=t("inbox") + "_pkey"),
    )

    meta.create_all(conn)
