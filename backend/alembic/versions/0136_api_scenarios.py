"""默认配置的「场景」开关：哪个场景允许用默认配置兜底

默认档每个 kind 只有一条，但「哪件事允许用它」是分开的产品决策（AI 对话兜底、
群世界调度、智能整理、创建助手、语音兜底）。以前这些散在各业务线的 if 里，
说不清有几处；现在清单收在 app/utils/pure/api_scenarios.py，这张表只记
「用户关掉了哪个场景」——没有记录 = 开，所以新场景上线不用写数据迁移。

Revision ID: 0136
Revises: 0135
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0136"
down_revision: Union[str, None] = "0135"
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
    ("user_dm_preferences", "user_id"): CASCADE,
    ("user_group_preferences", "user_id"): CASCADE,
    ("user_plugin_prefs", "user_id"): CASCADE,
    ("vector_acceleration_requests", "requester_id"): CASCADE,
    ("world_agents", "agent_id"): CASCADE,
    ("world_market_items", "author_id"): SET_NULL,
    ("worlds", "owner_id"): CASCADE,
}


def upgrade() -> None:
    op.create_table(
        "user_api_scenario_off",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("scenario", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "scenario", name="uq_user_api_scenario_off"),
    )


def downgrade() -> None:
    op.drop_table("user_api_scenario_off")
