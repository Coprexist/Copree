"""从聊天记录导入：消息的通道标记 + 导入批次记账

两件事：

1. `dm_messages.via` —— 导入的历史混在私信里，要能和正常对话分开。
   不新造字段：群的 `messages.via` 早就是「NULL=站内；qq=QQ 通道」这套语义，
   私信补上同一列即可，渲染侧一个 `via === 'import'` 就够。
2. `chat_imports` —— 一次导入一行。用来标「这段来自导入」、统计无原文件数、
   以及将来整批撤销；正文不在这里（正文只在消息里，单一来源）。

Revision ID: 0135
Revises: 0134
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0135"
down_revision: Union[str, None] = "0134"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 删除语义的冻结快照：与 app/models/fk_policy.py 同源。
# 本次新增 chat_imports 的两个外键（都指 users / agents），所以快照也随之多两行——
# 规矩是「策略文件 → 模型列 → 新迁移（带上新的 FROZEN_POLICY）」，见 fk_policy.py 文件头。
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
    ("user_dm_preferences", "user_id"): CASCADE,
    ("user_group_preferences", "user_id"): CASCADE,
    ("user_plugin_prefs", "user_id"): CASCADE,
    ("vector_acceleration_requests", "requester_id"): CASCADE,
    ("world_agents", "agent_id"): CASCADE,
    ("world_market_items", "author_id"): SET_NULL,
    ("worlds", "owner_id"): CASCADE,
}


def upgrade() -> None:
    op.add_column("dm_messages", sa.Column("via", sa.String(16), nullable=True))

    op.create_table(
        "chat_imports",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("agent_id", sa.Integer(), nullable=False),
        sa.Column("creator_user_id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.String(64), nullable=False),
        sa.Column("platform", sa.String(32), nullable=False),
        sa.Column("parser", sa.String(64), nullable=False),
        sa.Column("parser_version", sa.Integer(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("title", sa.String(200), nullable=True),
        sa.Column("message_count", sa.Integer(), nullable=False),
        sa.Column("attachment_count", sa.Integer(), nullable=False),
        sa.Column("missing_attachment_count", sa.Integer(), nullable=False),
        sa.Column("first_at", sa.DateTime(), nullable=True),
        sa.Column("last_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["creator_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_id"], ["dm_sessions.session_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_chat_imports_agent", "chat_imports", ["agent_id"])
    op.create_index("ix_chat_imports_session", "chat_imports", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_chat_imports_session", table_name="chat_imports")
    op.drop_index("ix_chat_imports_agent", table_name="chat_imports")
    op.drop_table("chat_imports")
    op.drop_column("dm_messages", "via")
