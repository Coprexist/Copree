"""api_usage_log.pool_key_id: 删池 Key 时置空引用（不再 500）

病根：外键没有 ON DELETE 规则，池 Key 一旦有用量记录就删不掉，接口抛成 500
（线上实测 DELETE /admin/api-key-pool/1 → ForeignKeyViolationError）。
语义：用量历史要留，删 Key 只把引用置空（列本来就可空）。

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
"""
from alembic import op

revision = "b8c9d0e1f2a3"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None

FK = "api_usage_log_pool_key_id_fkey"


def upgrade():
    op.drop_constraint(FK, 'api_usage_log', type_='foreignkey')
    op.create_foreign_key(FK, 'api_usage_log', 'api_key_pool', ['pool_key_id'], ['id'],
                          ondelete='SET NULL')


def downgrade():
    op.drop_constraint(FK, 'api_usage_log', type_='foreignkey')
    op.create_foreign_key(FK, 'api_usage_log', 'api_key_pool', ['pool_key_id'], ['id'])