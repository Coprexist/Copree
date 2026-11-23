"""补上自习室两张表的迁移（模型一直在，迁移链一直没有）

study_records / study_settings 是 2026-08 随自习室一起进模型的，当时建表写在同一
提交的 prestart.py 补丁清单里；后来 prestart 收敛成「只在迁移集合变化时跑 alembic」，
那份补丁清单被删掉，这两张表也就从迁移链上消失了：

  - create_all 建的新库有它们（模型在，表就在）；
  - 老库升到 head 也没有它们——/study 任何一次访问都是 relation does not exist。

而且 prestart 补丁建出来的版本没有指向 users 的外键，删号会留下孤儿行。

这里按模型补建（表已存在就只补缺的那部分），user_id 的删除语义按 fk_policy 对齐成
ON DELETE CASCADE：自习数据是用户从属数据，人没了记录不该留着。

Revision ID: 0127
Revises: 0126
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0127"
down_revision: Union[str, None] = "0126"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(bind, name: str) -> bool:
    return name in set(sa.inspect(bind).get_table_names())


def _fk_defs(bind, table: str) -> dict[str, str]:
    """user_id 上的外键：约束名 → 定义（判断有没有 ondelete 用）。"""
    rows = bind.execute(sa.text("""
        select con.conname, pg_get_constraintdef(con.oid) as cdef
        from pg_constraint con
        join pg_class rel on rel.oid = con.conrelid
        join pg_attribute att on att.attrelid = con.conrelid and att.attnum = con.conkey[1]
        where con.contype = 'f' and rel.relname = :t and att.attname = 'user_id'
    """), {"t": table}).fetchall()
    return {r.conname: r.cdef for r in rows}


def _ensure_user_fk(bind, table: str) -> None:
    """user_id → users.id，ON DELETE CASCADE。旧的无 ondelete 版本先拆掉。"""
    defs = _fk_defs(bind, table)
    if any("ON DELETE CASCADE" in d for d in defs.values()):
        return
    for name in defs:
        op.drop_constraint(name, table, type_="foreignkey")
    op.create_foreign_key(
        f"{table}_user_id_fkey", table, "users", ["user_id"], ["id"], ondelete="CASCADE",
    )


def upgrade() -> None:
    bind = op.get_bind()

    if not _has_table(bind, "study_records"):
        op.create_table(
            "study_records",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("date", sa.String(10), nullable=False, comment="YYYY-MM-DD"),
            sa.Column("minutes", sa.Integer(), nullable=False, server_default="0",
                      comment="当日累计学习分钟"),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
            sa.UniqueConstraint("user_id", "date", name="uq_study_user_date"),
        )

    if not _has_table(bind, "study_settings"):
        op.create_table(
            "study_settings",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.Integer(), nullable=False, unique=True),
            sa.Column("focus", sa.Integer(), nullable=False, server_default="25",
                      comment="专注时长（分钟）"),
            sa.Column("short", sa.Integer(), nullable=False, server_default="5",
                      comment="短休息时长（分钟）"),
            sa.Column("long", sa.Integer(), nullable=False, server_default="15",
                      comment="长休息时长（分钟）"),
            sa.Column("interval", sa.Integer(), nullable=False, server_default="4",
                      comment="长休息间隔（专注次数，1~12）"),
            sa.Column("cycle_date", sa.String(10), comment="周期进度所属日期 YYYY-MM-DD"),
            sa.Column("cycles", sa.Integer(), nullable=False, server_default="0",
                      comment="当前轮已完成专注数"),
            sa.Column("sessions", sa.Integer(), nullable=False, server_default="0",
                      comment="今日累计完成专注数"),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        )

    _ensure_user_fk(bind, "study_records")
    _ensure_user_fk(bind, "study_settings")


def downgrade() -> None:
    # 只拆外键、不删表：降级后老代码仍要能读到自习数据，删表是不可逆的数据损失
    bind = op.get_bind()
    for table in ("study_records", "study_settings"):
        if not _has_table(bind, table):
            continue
        for name in _fk_defs(bind, table):
            op.drop_constraint(name, table, type_="foreignkey")
        op.create_foreign_key(f"{table}_user_id_fkey", table, "users", ["user_id"], ["id"])
