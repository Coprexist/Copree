"""场景默认配置：有序序列 + 失败状态 + AI 级优先开关

三件事，对应 docs/dev/api_config_chain.md §3.4 与 §4.2 的 0137：

1. user_api_scenario_routes  —— 每个场景一条有序的配置序列，取代全局默认档。
2. user_api_scenario_status  —— 每格最近一次尝试结果，供红点与站内消息（阅读后才清）。
3. agents.prefer_own_config  —— AI 级「优先使用本 AI 的配置」，NULL = 跟随用户设置。

数据迁移把这之前 is_default 的那条档，复制进该用户**每一个**对应场景的第一格：
这就是「全局默认档取消、迁移到所有情景下」的落点。is_default 列不清空、只读，
留着对账与回滚。

Revision ID: 0137
Revises: 0136
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0137"
down_revision: Union[str, None] = "0136"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 删除语义的冻结快照：与 app/models/fk_policy.py 同源（规矩见 fk_policy.py 文件头）。
CASCADE = "CASCADE"
SET_NULL = "SET NULL"

FROZEN_POLICY: dict[tuple[str, str], str] = {
    ("agent_alarms", "agent_id"): CASCADE,
    ("agent_attention", "agent_id"): CASCADE,
    ("agent_collaborators", "agent_id"): CASCADE,
    ("agent_collaborators", "user_id"): CASCADE,
    ("agent_config_history", "agent_id"): CASCADE,
    ("agent_configs", "agent_id"): CASCADE,
    ("agent_creation_drafts", "user_id"): CASCADE,
    ("agent_emoji_packs", "agent_id"): CASCADE,
    ("agent_history_entries", "agent_id"): CASCADE,
    ("agent_history_seq", "agent_id"): CASCADE,
    ("agent_knowledge", "agent_id"): CASCADE,
    ("agent_skill_relations", "agent_id"): CASCADE,
    ("agent_skills", "agent_id"): CASCADE,
    ("agent_state_chain", "agent_id"): CASCADE,
    ("agent_state_chain_node", "agent_id"): CASCADE,
    ("agent_state_stack", "agent_id"): CASCADE,
    ("agent_triggers", "agent_id"): CASCADE,
    ("agent_user_configs", "agent_id"): CASCADE,
    ("agent_user_configs", "user_id"): CASCADE,
    ("agent_voice_configs", "agent_id"): CASCADE,
    ("agent_workspace", "agent_id"): CASCADE,
    ("agents", "owner_id"): CASCADE,
    ("agents", "user_id"): SET_NULL,
    ("ai_conversation_logs", "agent_id"): SET_NULL,
    ("ai_conversation_logs", "user_id"): CASCADE,
    ("api_usage_log", "agent_id"): SET_NULL,
    ("api_usage_log", "user_id"): SET_NULL,
    ("channel_conversation_usage", "agent_id"): CASCADE,
    ("channel_token_policy", "agent_id"): CASCADE,
    ("chat_imports", "agent_id"): CASCADE,
    ("chat_imports", "creator_user_id"): CASCADE,
    ("context_segments", "agent_id"): CASCADE,
    ("conversation_log_config", "updated_by"): SET_NULL,
    ("dm_messages", "sender_id"): CASCADE,
    ("dm_sessions", "user1_id"): CASCADE,
    ("dm_sessions", "user2_id"): CASCADE,
    ("external_identities", "bound_user_id"): SET_NULL,
    ("external_identities", "user_id"): SET_NULL,
    ("group_invitations", "approved_by"): SET_NULL,
    ("group_invitations", "invitee_id"): CASCADE,
    ("group_invitations", "inviter_id"): CASCADE,
    ("group_join_requests", "resolver_id"): SET_NULL,
    ("group_join_requests", "user_id"): CASCADE,
    ("member_silences", "agent_id"): CASCADE,
    ("opencli_agent_whitelist", "agent_id"): CASCADE,
    ("opencli_command_whitelist", "created_by"): SET_NULL,
    ("opencli_config", "updated_by"): SET_NULL,
    ("opencli_usage_log", "agent_id"): SET_NULL,
    ("pending_messages", "agent_id"): CASCADE,
    ("personality_anchors", "agent_id"): CASCADE,
    ("proxy_judgements", "agent_id"): CASCADE,
    ("redemption_codes", "created_by"): SET_NULL,
    ("redemption_codes", "used_by"): SET_NULL,
    ("redemption_records", "user_id"): CASCADE,
    ("rough_memories", "user_id"): CASCADE,
    ("structured_records", "agent_id"): CASCADE,
    ("study_records", "user_id"): CASCADE,
    ("study_settings", "user_id"): CASCADE,
    ("system_settings", "updated_by"): SET_NULL,
    ("unread_summary_cache", "agent_id"): CASCADE,
    ("user_api_assignments", "user_id"): CASCADE,
    ("user_api_profiles", "user_id"): CASCADE,
    ("user_api_providers", "user_id"): CASCADE,
    ("user_api_scenario_off", "user_id"): CASCADE,
    ("user_api_scenario_routes", "user_id"): CASCADE,
    ("user_api_scenario_status", "user_id"): CASCADE,
    ("user_dm_preferences", "user_id"): CASCADE,
    ("user_group_preferences", "user_id"): CASCADE,
    ("user_plugin_prefs", "user_id"): CASCADE,
    ("vector_acceleration_requests", "requester_id"): CASCADE,
    ("world_agents", "agent_id"): CASCADE,
    ("world_market_items", "author_id"): SET_NULL,
    ("worlds", "owner_id"): CASCADE,
}

# 场景清单在这里写死：迁移是历史快照，不 import 应用层清单——
# 否则以后增删场景会改变这条迁移在旧库上的行为。与 FROZEN_POLICY 同一个理由。
LLM_SCENARIOS = ("agent_llm", "world_ai", "smart_organize", "agent_create")


def upgrade() -> None:
    op.create_table(
        "user_api_scenario_routes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("scenario", sa.String(32), nullable=False),
        sa.Column("profile_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["profile_id"], ["user_api_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "scenario", "profile_id", name="uq_user_api_scenario_route"),
    )

    op.create_table(
        "user_api_scenario_status",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("scenario", sa.String(32), nullable=False),
        sa.Column("profile_id", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(16), server_default="ok", nullable=False),
        sa.Column("reason", sa.String(32), nullable=True),
        sa.Column("unread", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("tried_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["profile_id"], ["user_api_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "scenario", "profile_id", name="uq_user_api_scenario_status"),
    )

    op.add_column("agents", sa.Column("prefer_own_config", sa.Boolean(), nullable=True))

    # 全局默认档 → 每个场景的第一格。按厂商 kind 决定进哪一组场景。
    for scenario in LLM_SCENARIOS:
        op.execute(sa.text(f"""
            INSERT INTO user_api_scenario_routes (user_id, scenario, profile_id, position, enabled, created_at)
            SELECT p.user_id, '{scenario}', p.id, 1, true, now()
            FROM user_api_profiles p
            JOIN user_api_providers pr ON pr.id = p.provider_id
            WHERE p.is_default = true AND pr.kind = 'llm'
            ON CONFLICT DO NOTHING
        """))
    op.execute(sa.text("""
        INSERT INTO user_api_scenario_routes (user_id, scenario, profile_id, position, enabled, created_at)
        SELECT p.user_id, 'voice_default', p.id, 1, true, now()
        FROM user_api_profiles p
        JOIN user_api_providers pr ON pr.id = p.provider_id
        WHERE p.is_default = true AND pr.kind = 'voice'
        ON CONFLICT DO NOTHING
    """))


def downgrade() -> None:
    op.drop_column("agents", "prefer_own_config")
    op.drop_table("user_api_scenario_status")
    op.drop_table("user_api_scenario_routes")
