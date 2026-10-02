"""用量日聚合表

用量统计原先直接从对话日志聚合，而对话日志按条数滚动删除，活跃 AI 只能看到最近一两天。
这张表按「日期 + AI + 模型」每天一行累计，只增不删；升级时把现存日志聚合回填一次，
让还没被裁掉的历史立刻回到用量页上。

Revision ID: 0073
Revises: 0072
Create Date: 2026-09-28
"""
import sqlalchemy as sa
from alembic import op

revision = "0073"
down_revision = "0072"
branch_labels = None
depends_on = None

# 回填与运行时用同一条口径：没有任何 token/call 的轮次不占行，
# 否则「有记录的天数」会被空轮次灌水
_BACKFILL = """
INSERT INTO usage_daily (
    stat_date, agent_id, user_id, model,
    total_tokens, prompt_tokens, completion_tokens,
    reasoning_tokens, cached_tokens, api_calls, updated_at
)
SELECT
    DATE(created_at),
    COALESCE(agent_id, 0),
    COALESCE(user_id, 0),
    COALESCE(model, ''),
    COALESCE(SUM((token_usage->>'total_tokens')::bigint), 0),
    COALESCE(SUM((token_usage->>'prompt_tokens')::bigint), 0),
    COALESCE(SUM((token_usage->>'completion_tokens')::bigint), 0),
    COALESCE(SUM((token_usage->>'reasoning_tokens')::bigint), 0),
    COALESCE(SUM((token_usage->>'cached_tokens')::bigint), 0),
    COALESCE(SUM((token_usage->>'api_calls')::int), 0),
    now()
FROM ai_conversation_logs
GROUP BY 1, 2, 3, 4
HAVING COALESCE(SUM((token_usage->>'api_calls')::int), 0) > 0
    OR COALESCE(SUM((token_usage->>'total_tokens')::bigint), 0) > 0
"""


def upgrade() -> None:
    op.create_table(
        "usage_daily",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("stat_date", sa.Date(), nullable=False, comment="统计日期（UTC）"),
        sa.Column("agent_id", sa.Integer(), nullable=False, server_default="0",
                  comment="AI id；0 = 世界 AI 等没有 agent 行的记账"),
        sa.Column("user_id", sa.Integer(), nullable=False, server_default="0",
                  comment="世界 AI 的记账人；普通 AI 留 0，归属查询时走 agents.owner_id"),
        sa.Column("model", sa.String(length=50), nullable=False, server_default="",
                  comment="模型名；空串 = 未知"),
        sa.Column("total_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("prompt_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("reasoning_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("cached_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("api_calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stat_date", "agent_id", "user_id", "model", name="uq_usage_daily_key"),
    )

    # 新库走 create_all + stamp head，没有历史可回填；SQLite 也没有 jsonb 操作符
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(_BACKFILL)


def downgrade() -> None:
    op.drop_table("usage_daily")
