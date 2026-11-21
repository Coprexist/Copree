"""互动流水：B 站消息中心里看见的每一条都留一份（回复 / @ / 赞 / 私信）。

为什么要有这张表：B 站的消息接口只给「当前一窗」，没有按时间取的能力
（`x/msgfeed/reply` 带上 `id`+`reply_time` 也只能往回翻，不能按区间筛）。所以
「过去三天谁回了我、哪个帖子最热闹」只能问自己：扫到就存，查的时候只读这张表——
O(区间里的条数) 的本地索引扫描，零网络。

两条要点：
- `ts` 是**消息自己的时间**（reply_time / like_time / 私信时间），不是我们读到它的时间。
  补扫历史时两者能差三年，"最近七天"按读的时间算会把三年前的回复算进来；
- `item_key` 是站点给的那条消息的稳定身份：回复/@ 用 rpid，赞用 `<id>:<like_time>`
  （同一个目标被再赞一次 id 不变、时间变新），私信用 `<talker_id>:<msg_seqno>`。
  (instance, kind, item_key) 唯一，重复扫到同一条不会变成两条。

存的是**渲染要什么就存什么**（谁、在哪、说了什么、赞数都是按行的口径存好的），
查询时不再回头解析 B 站的原始结构。
"""
import sqlalchemy as sa


def upgrade(conn, t) -> None:
    meta = sa.MetaData()

    table = sa.Table(
        t("feed"), meta,
        sa.Column("instance", sa.String(64), nullable=False, comment="一个 AI 一个实例"),
        sa.Column("kind", sa.String(12), nullable=False, comment="reply / at / like / dm"),
        sa.Column("item_key", sa.String(64), nullable=False,
                  comment="站点给的那条消息的身份：rpid / <id>:<like_time> / <talker>:<seqno>"),
        sa.Column("ts", sa.BigInteger, nullable=False, server_default="0",
                  comment="消息自己的时间（epoch 秒），不是读到它的时间"),
        sa.Column("who", sa.String(128), nullable=False, server_default="",
                  comment="已渲染好的人：昵称，赞是「甲 等 N 人」"),
        sa.Column("target", sa.String(64), nullable=False, server_default="",
                  comment="聚类的落点：BV 号 / 私信的对方"),
        sa.Column("title", sa.String(120), nullable=False, server_default="",
                  comment="没有 BV 号时（动态、专栏）退回用它"),
        sa.Column("said", sa.String(200), nullable=False, server_default="",
                  comment="说了什么（已截断）"),
        sa.Column("root", sa.String(32), nullable=False, server_default="",
                  comment="楼中楼挂在哪个 rpid 下"),
        sa.Column("counts", sa.Integer, nullable=False, server_default="0",
                  comment="赞的累计数（其他类别为 0）"),
        sa.Column("seen_at", sa.BigInteger, nullable=False, server_default="0",
                  comment="我们第一次看到它的时间（epoch 秒）"),
        sa.PrimaryKeyConstraint("instance", "kind", "item_key", name=t("feed") + "_pkey"),
    )
    # 区间查询就靠这一个索引：WHERE instance = ? AND ts >= ? AND ts < ?
    sa.Index(t("feed") + "_ts_idx", table.c.instance, table.c.ts)

    meta.create_all(conn)
