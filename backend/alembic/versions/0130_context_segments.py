"""上下文压缩段快照表

背景：压缩后只留「中间 N 条已折叠」一句提示，而提示里指向的 expand_context 工具
并不存在（2026-11-25 排查确认）。补工具前得先有原文——这张表存压缩前的 messages。

Revision ID: 0130
Revises: 0129
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0130"
down_revision: Union[str, None] = "0129"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# 删除语义快照：与 app/models/fk_policy.py 一致。改语义的流程是
# 策略文件 → 模型列 → 新迁移（新迁移带上新的 FROZEN_POLICY），见 fk_policy.py 文件头。
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
    ("channel_conversation_usage", "agent_id"): CASCADE,
    ("channel_token_policy", "agent_id"): CASCADE,
    ("context_segments", "agent_id"): CASCADE,
    ("member_silences", "agent_id"): CASCADE,
    ("opencli_agent_whitelist", "agent_id"): CASCADE,
    ("pending_messages", "agent_id"): CASCADE,
    ("personality_anchors", "agent_id"): CASCADE,
    ("proxy_judgements", "agent_id"): CASCADE,
    ("structured_records", "agent_id"): CASCADE,
    ("unread_summary_cache", "agent_id"): CASCADE,
    ("vector_acceleration_requests", "requester_id"): CASCADE,
    ("world_agents", "agent_id"): CASCADE,
    ("agents", "owner_id"): CASCADE,
    ("agents", "user_id"): SET_NULL,
    ("dm_messages", "sender_id"): CASCADE,
    ("dm_sessions", "user1_id"): CASCADE,
    ("dm_sessions", "user2_id"): CASCADE,
    ("group_invitations", "invitee_id"): CASCADE,
    ("group_invitations", "inviter_id"): CASCADE,
    ("group_join_requests", "user_id"): CASCADE,
    ("redemption_records", "user_id"): CASCADE,
    ("rough_memories", "user_id"): CASCADE,
    ("study_records", "user_id"): CASCADE,
    ("study_settings", "user_id"): CASCADE,
    ("user_api_assignments", "user_id"): CASCADE,
    ("user_api_profiles", "user_id"): CASCADE,
    ("user_api_providers", "user_id"): CASCADE,
    ("user_dm_preferences", "user_id"): CASCADE,
    ("user_group_preferences", "user_id"): CASCADE,
    ("user_plugin_prefs", "user_id"): CASCADE,
    ("worlds", "owner_id"): CASCADE,
    ("ai_conversation_logs", "agent_id"): SET_NULL,
    ("ai_conversation_logs", "user_id"): CASCADE,
    ("api_usage_log", "agent_id"): SET_NULL,
    ("api_usage_log", "user_id"): SET_NULL,
    ("conversation_log_config", "updated_by"): SET_NULL,
    ("external_identities", "bound_user_id"): SET_NULL,
    ("external_identities", "user_id"): SET_NULL,
    ("group_invitations", "approved_by"): SET_NULL,
    ("group_join_requests", "resolver_id"): SET_NULL,
    ("opencli_command_whitelist", "created_by"): SET_NULL,
    ("opencli_config", "updated_by"): SET_NULL,
    ("opencli_usage_log", "agent_id"): SET_NULL,
    ("redemption_codes", "created_by"): SET_NULL,
    ("redemption_codes", "used_by"): SET_NULL,
    ("system_settings", "updated_by"): SET_NULL,
    ("world_market_items", "author_id"): SET_NULL,
}


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "context_segments" in tables:
        return
    op.create_table(
        "context_segments",
        sa.Column("id", sa.String(16), primary_key=True),
        sa.Column("agent_id", sa.Integer(),
                  sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("context_ref", sa.String(128), nullable=False),
        sa.Column("conversation_type", sa.String(32), nullable=False, server_default="group"),
        sa.Column("group_id", sa.Integer(), nullable=True),
        sa.Column("session_id", sa.String(50), nullable=True),
        sa.Column("message_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("token_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_context_segments_agent_id", "context_segments", ["agent_id"])
    op.create_index("ix_context_segments_context_ref", "context_segments", ["context_ref"])
    op.create_index("ix_context_segments_created_at", "context_segments", ["created_at"])
    op.create_index("ix_context_segments_expires_at", "context_segments", ["expires_at"])
    op.create_index("ix_context_segments_agent_created", "context_segments",
                    ["agent_id", "created_at"])


def downgrade() -> None:
    op.drop_table("context_segments")
