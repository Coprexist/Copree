"""把数据库时区固定为 UTC

全库时间戳列都是无时区 timestamp，值一律按 UTC 存。DB 时区若漂移，
func.now() 写进去的就不再是 UTC，那些无时区值会被按错误的时区解释。

用 DO 块取当前库名（ALTER DATABASE 要标识符字面量）；权限不足时只告警，
不阻塞启动——部署角色通常就是库 owner，这条会正常生效。

Revision ID: 0071
Revises: 0070
Create Date: 2026-09-26
"""
from alembic import op

revision = "0071"
down_revision = "0070"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
            EXECUTE format('ALTER DATABASE %I SET timezone TO ''UTC''', current_database());
        EXCEPTION WHEN insufficient_privilege THEN
            RAISE NOTICE '跳过：当前角色无权修改数据库时区';
        END $$;
    """)


def downgrade() -> None:
    # 时区是环境约定，不回滚
    pass
