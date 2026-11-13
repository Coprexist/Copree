"""NapCat 通道自己的账号记录：每个实例上次挂过的号

为什么进表不进文件：状态接口会被前端轮询、被顶下线时也要读，文件是整读整写、只能靠
文件名区分实例；表有唯一键（一个实例一行）与行级原子性。卸载插件时按 plugin_qq_napcat__
前缀就能枚举，保数据还是清干净由管理员选。

为什么不搬老文件：本表上线前，同一个号曾短暂写进 plugin_data/qq-napcat/account-<实例>.json
的中间版本，但那个版本从未上线（仓库里未提交、线上没有该目录），没有数据可搬；将来若
某个部署真有这个文件，处理方式是重新扫码一次，而不是让本迁移长期依赖平台目录布局。
"""
import sqlalchemy as sa


def upgrade(conn, t) -> None:
    meta = sa.MetaData()

    sa.Table(
        t("accounts"), meta,
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("instance", sa.String(40), nullable=False, comment="通道实例（agent-<id>）"),
        sa.Column("uin", sa.String(20), nullable=False, server_default="", comment="上次登录的 QQ 号"),
        sa.Column("nick", sa.String(80), nullable=False, server_default="", comment="昵称（页面直接显示）"),
        sa.Column("avatar", sa.String(400), nullable=False, server_default="", comment="头像地址"),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("instance", name=t("accounts_instance_key")),
    )

    meta.create_all(conn)
