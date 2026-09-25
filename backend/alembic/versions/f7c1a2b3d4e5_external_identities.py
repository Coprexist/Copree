"""外部身份独立成表（channel_pairings 并入）

原来 QQ 通道给每个 openid 建一个 users 影子账号（占 users.id、混进用户统计与搜人），
配对状态单开 channel_pairings。这两件事其实是同一行数据：这个外部身份是谁、放不放行。
这里合并成 external_identities。

已有影子账号就地标成 type='external'：他们不再被当成真人（搜人/统计/注册引导），
users.id 的回收要等消息与私信都改成引用外部身份之后单独做 —— 那一步会动消息表，
不适合和数据搬家混在一起。

Revision ID: f7c1a2b3d4e5
Revises: f0a1b2c3d4e5
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f7c1a2b3d4e5"
down_revision: Union[str, None] = "f0a1b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "external_identities",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("kind", sa.String(length=80), nullable=False, comment="通道/来源种类，如 qq-channel、federation"),
        sa.Column("owner_scope", sa.String(length=120), server_default="", nullable=False,
                  comment="该通道下的归属实例：QQ 是 agent-<agentId>，联邦是对端公网 ID"),
        sa.Column("origin", sa.String(length=200), nullable=False,
                  comment="通道侧的稳定标识：QQ 是 openid，联邦是远端实体 ID"),
        sa.Column("display_name", sa.String(length=120), nullable=True, comment="通道侧昵称，仅用于展示"),
        sa.Column("avatar_url", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=True,
                  comment="pending | approved | blocked"),
        sa.Column("code", sa.String(length=12), nullable=True, comment="配对码：用户在通道里收到，回平台核对"),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("bound_user_id", sa.Integer(), nullable=True,
                  comment="绑定到本实例的真实账号（他后来自己注册了）"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True, comment="最近一次收到他的消息"),
        sa.ForeignKeyConstraint(["bound_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("kind", "owner_scope", "origin", name="uq_external_identity"),
    )
    op.create_index("ix_external_identity_lookup", "external_identities", ["kind", "owner_scope", "status"])

    # 配对记录就是外部身份那一行：原地搬过去，id 也保留（前端拿它点批准/拉黑）
    op.execute("""
        INSERT INTO external_identities
            (id, kind, owner_scope, origin, display_name, status, code, approved_at, created_at, updated_at)
        -- kind 现在由插件的 manifest 声明（qq-channel → qq）；历史行就地改名，不做第二套常量
        SELECT id, CASE WHEN plugin_id = 'qq-channel' THEN 'qq' ELSE plugin_id END, instance, openid, COALESCE(nickname, ''),
               COALESCE(NULLIF(status, ''), 'pending'), COALESCE(code, ''),
               approved_at, created_at, updated_at
        FROM channel_pairings
    """)
    op.execute("SELECT setval(pg_get_serial_sequence('external_identities', 'id'), "
               "GREATEST((SELECT COALESCE(MAX(id), 0) FROM external_identities), 1))")

    op.drop_index("ix_channel_pairings_lookup", table_name="channel_pairings")
    op.drop_table("channel_pairings")

    op.drop_constraint("users_type_check", "users", type_="check")
    op.create_check_constraint("users_type_check", "users", "type IN ('human', 'ai', 'system', 'external')")
    op.execute("UPDATE users SET type = 'external' WHERE origin_channel IS NOT NULL")


def downgrade() -> None:
    op.execute("UPDATE users SET type = 'human' WHERE type = 'external'")
    op.drop_constraint("users_type_check", "users", type_="check")
    op.create_check_constraint("users_type_check", "users", "type IN ('human', 'ai', 'system')")

    op.create_table(
        "channel_pairings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("plugin_id", sa.String(length=80), nullable=False),
        sa.Column("instance", sa.String(length=40), server_default="", nullable=False),
        sa.Column("openid", sa.String(length=160), nullable=False),
        sa.Column("nickname", sa.String(length=120), nullable=True),
        sa.Column("code", sa.String(length=12), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plugin_id", "instance", "openid", name="uq_channel_pairing"),
    )
    op.create_index("ix_channel_pairings_lookup", "channel_pairings", ["plugin_id", "instance", "status"])
    op.execute("""
        INSERT INTO channel_pairings (id, plugin_id, instance, openid, nickname, code, status, approved_at, created_at, updated_at)
        SELECT id, CASE WHEN kind = 'qq' THEN 'qq-channel' ELSE kind END, owner_scope, origin,
               display_name, code, status, approved_at, created_at, updated_at
        FROM external_identities WHERE kind IN ('qq', 'qq-channel')
    """)
    op.drop_index("ix_external_identity_lookup", table_name="external_identities")
    op.drop_table("external_identities")
