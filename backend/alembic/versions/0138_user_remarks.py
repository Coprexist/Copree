"""用户备注：群备注与私信对端备注

一个对象至多一条备注，两种目标用 group_id / target_user_id 表达且 CHECK 互斥；
删除语义交给数据库（删群、删账号时备注跟着走），见 app/models/fk_policy.py。

Revision ID: 0138
Revises: 0137
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0138"
down_revision: Union[str, None] = "0137"
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
    ("user_remarks", "owner_id"): CASCADE,
    ("user_remarks", "target_user_id"): CASCADE,
    ("vector_acceleration_requests", "requester_id"): CASCADE,
    ("world_agents", "agent_id"): CASCADE,
    ("world_market_items", "author_id"): SET_NULL,
    ("worlds", "owner_id"): CASCADE,
}


def upgrade() -> None:
    op.create_table(
        "user_remarks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("group_id", sa.Integer(), nullable=True),
        sa.Column("target_user_id", sa.Integer(), nullable=True),
        sa.Column("remark", sa.String(32), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["group_id"], ["groups.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "(group_id IS NOT NULL) <> (target_user_id IS NOT NULL)",
            name="ck_user_remark_single_target",
        ),
        sa.UniqueConstraint("owner_id", "group_id", name="uq_user_remark_group"),
        sa.UniqueConstraint("owner_id", "target_user_id", name="uq_user_remark_user"),
    )


def downgrade() -> None:
    op.drop_table("user_remarks")
