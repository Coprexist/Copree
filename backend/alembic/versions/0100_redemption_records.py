"""redemption_records + usage_limit：兑换码支持不限人次的活动码

为什么：以前「用过没有」只有 redemption_codes.used_by 一列,一个码只能给一个人。
活动要的是「不限人次、有效期内每人各兑一次」,这要求账本能回答两件事:谁兑过、
这个码一共兑了多少次。拆出 redemption_records 之后:
  - 已兑人次   = count(records where code = ...)
  - 每人限一次 = UNIQUE(code, user_id),并发由数据库兜底
  - usage_limit: NULL = 不限人次、1 = 一次性(历史默认)、N = 前 N 名

回填:老码的 used_by/used_at 转成一条流水,并把老码的 usage_limit 置 1
(保持它们原本的一次性语义;新码不写这个值就是 NULL = 不限)。
used_by / used_at 两列保留不删:回滚到旧版本时数据还在,新代码不再读写它们。

Revision ID: 0100
Revises: 0099
"""
from alembic import op
import sqlalchemy as sa


revision = "0100"
down_revision = "0099"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "redemption_records",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("code", sa.String(length=32),
                  sa.ForeignKey("redemption_codes.code", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("quota_amount", sa.Integer(), nullable=False),
        sa.Column("code_type", sa.String(length=20), nullable=False),
        sa.Column("redeemed_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("code", "user_id", name="uq_redemption_record_user"),
    )
    op.create_index("ix_redemption_records_code", "redemption_records", ["code"])
    op.create_index("ix_redemption_records_user", "redemption_records", ["user_id"])

    op.add_column("redemption_codes", sa.Column("usage_limit", sa.Integer(), nullable=True))
    # 历史码一律「一次性」：与旧行为一致，不会因为迁移突然变成不限人次
    op.execute("UPDATE redemption_codes SET usage_limit = 1")

    op.execute("""
        INSERT INTO redemption_records (code, user_id, quota_amount, code_type, redeemed_at)
        SELECT code, used_by, quota_amount,
               CASE WHEN code_type = 'file_size' THEN 'file_quota'
                    ELSE COALESCE(NULLIF(code_type, ''), 'ai_quota') END,
               COALESCE(used_at, created_at, now())
        FROM redemption_codes
        WHERE used_by IS NOT NULL
    """)


def downgrade() -> None:
    op.drop_index("ix_redemption_records_user", table_name="redemption_records")
    op.drop_index("ix_redemption_records_code", table_name="redemption_records")
    op.drop_table("redemption_records")
    op.drop_column("redemption_codes", "usage_limit")
