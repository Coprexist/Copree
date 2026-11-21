"""B 站通道自己的两张表：订阅与使用轨迹

为什么进表不进文件：
- 订阅会被工具（add/remove）和轮询任务（更新 last_ts）同时改，JSON 文件是"读-改-写"，
  两个写撞在一起就吃掉对方的改动；表有行级原子性，还能按实例建索引直接查
- 轨迹会一直长，文件只能整文件读；表能只取最近 N 条，也能裁剪旧的

表名由平台的 storage.table_name() 给出（plugin_bilibili__*）：删插件时按前缀就能枚举干净，
所以这里不写死表名。
"""
import sqlalchemy as sa


def upgrade(conn, t) -> None:
    meta = sa.MetaData()

    sa.Table(
        t("subscriptions"), meta,
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("instance", sa.String(40), nullable=False, comment="通道实例（agent-<id>）"),
        sa.Column("mid", sa.Integer, nullable=False, comment="UP 主 mid"),
        sa.Column("name", sa.String(80), nullable=False, server_default="", comment="UP 主昵称（推送文案用）"),
        sa.Column("group_id", sa.Integer, nullable=False, comment="推到哪个 Copree 群"),
        sa.Column("last_ts", sa.BigInteger, nullable=False, server_default="0", comment="已推到的动态时间戳"),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("instance", "mid", "group_id", name=t("subscriptions_key")),
    )

    sa.Table(
        t("activity"), meta,
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("instance", sa.String(40), nullable=False, comment="通道实例（agent-<id>）"),
        sa.Column("action", sa.String(60), nullable=False, comment="工具名，或 push / login"),
        sa.Column("summary", sa.Text, nullable=False, server_default="", comment="给人扫一眼的一行"),
        sa.Column("ok", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("duration_ms", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )

    meta.create_all(conn)
    conn.execute(sa.text(
        f'CREATE INDEX IF NOT EXISTS {t("activity_recent")} '
        f'ON {t("activity")} (instance, id)'
    ))
