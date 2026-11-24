"""清空裸 Key 两档

0123 / 0124 把配置迁成配置档，但**从没清空原列**：

    users.api_base_url / api_key_encrypted / global_chat_model / global_work_model
    agents.api_base_url / api_key_encrypted / chat_model / work_model

于是同一份配置在库里存在两份。后果不是「永远不生效」，而是**解绑配置档之后
会掉回这些遗留值** —— 用户以为回到了「默认」，实际用的是迁移前那一刻的快照。
1.0 的解析链已经只读配置档（docs/dev/api_config_chain.md §3.3），这里把残留值清掉。

清空前先补两件事，否则会丢配置：
1. 有 Key 但没绑配置档的 AI → 给它建一条配置档并绑定（密文直接搬，ENCRYPTION_KEY 未变）；
2. 绑定的配置档 primary_model 为空、而 AI 自己记着 chat_model → 补进配置档。

downgrade 只还原列结构（列没删，只是清空），值不可逆 —— 迁移前先做库备份。

Revision ID: 0131
Revises: 0130
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0131"
down_revision: Union[str, None] = "0130"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PROFILE_NAME = "迁移的配置"


def _adopt_orphan_agent_keys(bind) -> None:
    """有 Key 但没绑配置档的 AI：把 Key 搬进一条配置档，别让清列把它删掉。"""
    from app.config import settings

    rows = bind.execute(sa.text(
        "SELECT id, owner_id, coalesce(api_base_url, '') AS base_url, "
        "api_key_encrypted, coalesce(chat_model, '') AS chat_model, "
        "coalesce(work_model, '') AS work_model "
        "FROM agents WHERE coalesce(api_key_encrypted, '') <> '' "
        "AND llm_profile_id IS NULL"
    )).mappings().all()

    for row in rows:
        base_url = row["base_url"] or settings.deepseek_base_url
        provider_id = bind.execute(sa.text(
            "SELECT id FROM user_api_providers "
            "WHERE user_id = :user_id AND kind = 'llm' AND base_url = :base_url "
            "ORDER BY id LIMIT 1"
        ), {"user_id": row["owner_id"], "base_url": base_url}).scalar()
        if provider_id is None:
            provider_id = bind.execute(sa.text(
                "INSERT INTO user_api_providers "
                "(user_id, kind, name, base_url, default_primary, default_secondary) "
                "VALUES (:user_id, 'llm', :name, :base_url, :primary, :secondary) "
                "RETURNING id"
            ), {
                "user_id": row["owner_id"], "name": base_url, "base_url": base_url,
                "primary": row["chat_model"] or None,
                "secondary": row["work_model"] or None,
            }).scalar()

        profile_id = bind.execute(sa.text(
            "SELECT id FROM user_api_profiles "
            "WHERE provider_id = :provider_id AND name = :name LIMIT 1"
        ), {"provider_id": provider_id, "name": PROFILE_NAME}).scalar()
        if profile_id is None:
            profile_id = bind.execute(sa.text(
                "INSERT INTO user_api_profiles "
                "(user_id, provider_id, name, api_key_encrypted, primary_model, secondary_model) "
                "VALUES (:user_id, :provider_id, :name, :key, :primary, :secondary) "
                "RETURNING id"
            ), {
                "user_id": row["owner_id"], "provider_id": provider_id, "name": PROFILE_NAME,
                "key": row["api_key_encrypted"],
                "primary": row["chat_model"] or None,
                "secondary": row["work_model"] or None,
            }).scalar()

        bind.execute(sa.text(
            "UPDATE agents SET llm_profile_id = :profile_id WHERE id = :agent_id"
        ), {"profile_id": profile_id, "agent_id": row["id"]})


def _backfill_agent_models(bind) -> None:
    """AI 自己记着的模型补进它绑定的配置档（只补空值，不覆盖已有选择）。"""
    bind.execute(sa.text(
        "UPDATE user_api_profiles SET primary_model = ("
        "  SELECT a.chat_model FROM agents a WHERE a.llm_profile_id = user_api_profiles.id"
        ") WHERE coalesce(primary_model, '') = '' AND EXISTS ("
        "  SELECT 1 FROM agents a WHERE a.llm_profile_id = user_api_profiles.id "
        "  AND coalesce(a.chat_model, '') <> ''"
        ")"
    ))
    bind.execute(sa.text(
        "UPDATE user_api_profiles SET secondary_model = ("
        "  SELECT a.work_model FROM agents a WHERE a.llm_profile_id = user_api_profiles.id"
        ") WHERE coalesce(secondary_model, '') = '' AND EXISTS ("
        "  SELECT 1 FROM agents a WHERE a.llm_profile_id = user_api_profiles.id "
        "  AND coalesce(a.work_model, '') <> ''"
        ")"
    ))


def upgrade() -> None:
    bind = op.get_bind()
    _adopt_orphan_agent_keys(bind)
    _backfill_agent_models(bind)

    # 清空：解析链已经不读它们；留着只会让「解绑后掉回旧快照」重现
    bind.execute(sa.text(
        "UPDATE users SET api_base_url = NULL, api_key_encrypted = NULL, "
        "global_chat_model = NULL, global_work_model = NULL"
    ))
    bind.execute(sa.text(
        "UPDATE agents SET api_base_url = NULL, api_key_encrypted = NULL, "
        "chat_model = NULL, work_model = NULL"
    ))


def downgrade() -> None:
    # 值不可逆（迁移前应已备份）。列本身没有删，所以这里无事可做。
    pass
