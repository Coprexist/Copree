"""发出去的评论还没落定：先记账，公开了再告诉 AI。

B 站把新评论先标成隐藏/折叠（2026-11-01 实测：提交后立刻查是 state=17，几秒后转 state=0），
所以工具不能提交完就报"公开了"，也不该把这几十秒耗在工具调用里——AI 的整轮对话会被卡住。
改成：提交成功就返回「发布中」，同时在这里记一行，服务轮询追认，落定后往 AI 账本投一条通知
（见 plugin.api.report_notice）。没通知就是没公开，AI 不该把它说成已经公开。

行在落定后删除：这份表只装"还没结论"的评论，不承担历史（历史在用轨迹里）。
created_at 存 epoch 秒而不是 timezone 时间戳：追认要算年龄，直接相减最省事，也躲开时区口径。
"""
import sqlalchemy as sa


def upgrade(conn, t) -> None:
    meta = sa.MetaData()

    sa.Table(
        t("pending"), meta,
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("instance", sa.String(40), nullable=False, comment="通道实例（agent-<id>）"),
        sa.Column("agent_id", sa.Integer, nullable=False, comment="发评论的 AI"),
        sa.Column("group_id", sa.Integer, nullable=True, comment="在哪说的（群聊）"),
        sa.Column("session_id", sa.String(80), nullable=False, server_default="", comment="私信会话；群聊为空"),
        sa.Column("bvid", sa.String(32), nullable=False),
        sa.Column("aid", sa.BigInteger, nullable=False),
        sa.Column("rpid", sa.BigInteger, nullable=False, comment="这条评论的 rpid"),
        sa.Column("message", sa.Text, nullable=False, server_default="", comment="发出去的正文，通知里引用"),
        sa.Column("created_at", sa.BigInteger, nullable=False, comment="epoch 秒"),
        sa.Column("checked_at", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("tries", sa.Integer, nullable=False, server_default="0"),
    )

    meta.create_all(conn)
    conn.execute(sa.text(
        f'CREATE INDEX IF NOT EXISTS {t("pending_open")} '
        f'ON {t("pending")} (instance, id)'
    ))
