"""指向 users / agents 的外键，补齐删除语义（第二处系统级补齐）

背景：全库 67 个指向 users.id / agents.id 的外键里，有 20 个没写 ondelete，
PostgreSQL 默认 NO ACTION——于是「先删谁后删谁」成了散在业务代码注释里的隐式契约，
换一行顺序、或新开一条删除路径就是 500（2026-11-15 删除 AI 卡在 dm_messages）。

策略的唯一真源是 app/models/fk_policy.py：
  CASCADE   从属数据（主体没了它就不该存在）
  SET NULL  事实记录（用量、审计、兑换码、商城作品）

本迁移按策略落库，另外做三件事：
1. api_usage_log.user_id / redemption_codes.created_by / world_market_items.author_id
   改成可空——SET NULL 要求列允许为空；它们原来 NOT NULL 只是因为以前不会有人被删。
2. group_invitations.approved_by 补上外键：模型早就声明了 FK，但当年建列时只写了
   sa.Column(...) 没写约束，create_all 的新库有这个 FK、老库没有。
3. 已经是对目标语义的列直接跳过（迁移可重跑，不重复拆建约束）。

downgrade 只把外键恢复成 NO ACTION，不回退可空性：SET NULL 生效期间可能已经产生了
NULL，改回 NOT NULL 会直接失败；可空比约束多留一条不归路更安全。

Revision ID: 0128
Revises: 0127
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0128"
down_revision: Union[str, None] = "0127"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CASCADE = "CASCADE"
SET_NULL = "SET NULL"

# 冻结快照 = app/models/fk_policy.py 在写这条迁移时的内容。
# 两边必须一致（tests/test_fk_delete_policy.py 会比对）；以后改删除语义要同时改
# 策略文件、模型列、并新增一条迁移（新迁移也要带 FROZEN_POLICY）。
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

# 需要先放开可空性的列（SET NULL 写不进 NOT NULL 的列）
_NULLABLE_FIRST: tuple[tuple[str, str], ...] = (
    ("api_usage_log", "user_id"),
    ("redemption_codes", "created_by"),
    ("world_market_items", "author_id"),
)

# 模型声明了外键、库里可能只有列没有约束的那一个
_MISSING_FK: tuple[tuple[str, str], ...] = (("group_invitations", "approved_by"),)


def _fk_rows(bind, table: str, column: str) -> list[tuple[str, str]]:
    """这一列上现有的外键约束：(名字, 定义)。"""
    rows = bind.execute(sa.text("""
        select con.conname, pg_get_constraintdef(con.oid) as cdef
        from pg_constraint con
        join pg_class rel on rel.oid = con.conrelid
        join pg_attribute att on att.attrelid = con.conrelid and att.attnum = con.conkey[1]
        where con.contype = 'f' and rel.relname = :t and att.attname = :c
    """), {"t": table, "c": column}).fetchall()
    return [(r.conname, r.cdef) for r in rows]


def _ref_table(column: str) -> str:
    """本迁移只覆盖两张被引用表：agent_id 指 agents，其余列（user_id 与审计/权限列）指 users。"""
    return "agents" if column == "agent_id" else "users"


def _set_ondelete(bind, table: str, column: str, ondelete: str | None) -> None:
    """把某一列的外键换成指定删除语义（None = 恢复 NO ACTION）。"""
    rows = _fk_rows(bind, table, column)
    if ondelete is not None and any(f"ON DELETE {ondelete}" in d for _, d in rows):
        return
    for name, _ in rows:
        op.drop_constraint(name, table, type_="foreignkey")
    op.create_foreign_key(
        f"{table}_{column}_fkey", table, _ref_table(column), [column], ["id"],
        ondelete=ondelete,
    )


def upgrade() -> None:
    bind = op.get_bind()

    for table, column in _NULLABLE_FIRST:
        op.alter_column(table, column, existing_type=sa.Integer(), nullable=True)

    for table, column in _MISSING_FK:
        if not _fk_rows(bind, table, column):
            _set_ondelete(bind, table, column, FROZEN_POLICY[(table, column)])

    for (table, column), ondelete in FROZEN_POLICY.items():
        _set_ondelete(bind, table, column, ondelete)


def downgrade() -> None:
    bind = op.get_bind()

    for (table, column), _ in FROZEN_POLICY.items():
        _set_ondelete(bind, table, column, None)

    # approved_by 在升级前没有外键，降级就还它原样
    for name, _ in _fk_rows(bind, "group_invitations", "approved_by"):
        op.drop_constraint(name, "group_invitations", type_="foreignkey")
