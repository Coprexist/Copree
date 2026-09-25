"""compression knobs: idle_threshold_percent + compress_target_percent

三档阈值里的两个「系数」从代码常量变成可配：
- idle_threshold_percent：T_idle 在 [T_post, T_hot] 上的插值系数（默认 1/e ≈ 37%）
- compress_target_percent：T_post = T_hot × 这个比例（默认 20%）
两列都可空：**NULL = 用代码默认**——默认值只留一处（常量），管理员改过的才落库。

Revision ID: a7b8c9d0e1f2
Revises: f1e2d3c4b5a6
"""
from alembic import op
import sqlalchemy as sa

revision = "a7b8c9d0e1f2"
down_revision = "f1e2d3c4b5a6"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('conversation_log_config', sa.Column('idle_threshold_percent', sa.Integer(), nullable=True))
    op.add_column('conversation_log_config', sa.Column('compress_target_percent', sa.Integer(), nullable=True))


def downgrade():
    op.drop_column('conversation_log_config', 'compress_target_percent')
    op.drop_column('conversation_log_config', 'idle_threshold_percent')