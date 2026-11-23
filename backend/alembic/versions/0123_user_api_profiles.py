"""用户级 API 厂商与配置档（两层），并把旧的全局 API 配置迁成「厂商 + 默认档」

结构见 app/models/api_profile.py：
  user_api_providers（在哪里请求、这家的默认 Key、模型清单）
  user_api_profiles （这套配置叫什么、覆盖哪把 Key / 哪个模型）

同时给 agents 与 agent_voice_configs 各加一列指向配置档，让单个 AI 能整包选一条。

数据迁移：users 那几列（api_base_url / api_key_encrypted / global_chat_model /
global_work_model）曾经就是「全局默认」，现在把它变成一条真厂商 + 一条默认档；
users 上的列保留不删（本轮仍被解析链当兜底读），但从此以配置档为准。

Revision ID: 0123
Revises: 0122
"""
import json
from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0123"
down_revision: Union[str, None] = "0122"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 与 models/api_profile.py 的 json_column() 同一口径
JSON_TYPE = sa.JSON().with_variant(JSONB, "postgresql")

# 内置预设的冻结快照（versions/_provider_presets_snapshot.json）
# 迁移不能问 app 里的活代码：预设以后改地址、加模型，已经升过级的库不会重跑这段，
# 而还没升级的库却会跑去问"今天的内置清单"——同一版迁移因此在不同时间给出不同结果。
_FROZEN_PRESETS = json.loads(
    Path(__file__).with_name("_provider_presets_snapshot.json").read_text(encoding="utf-8")
)["presets"]


def upgrade() -> None:
    op.create_table(
        "user_api_providers",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False, server_default="llm"),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("preset_key", sa.String(40), nullable=True),
        sa.Column("base_url", sa.Text(), nullable=False, server_default=""),
        sa.Column("default_api_key_encrypted", sa.Text(), nullable=True),
        sa.Column("options", JSON_TYPE, nullable=True),
        sa.Column("default_primary", sa.String(200), nullable=True),
        sa.Column("default_secondary", sa.String(200), nullable=True),
        sa.Column("thinking_supported", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("system_layout", sa.String(16), nullable=False, server_default="anywhere"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", "kind", "name", name="uq_user_api_provider_name"),
    )
    op.create_index("ix_user_api_providers_user", "user_api_providers", ["user_id", "kind"])

    op.create_table(
        "user_api_profiles",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("provider_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("api_key_encrypted", sa.Text(), nullable=True),
        sa.Column("primary_model", sa.String(200), nullable=True),
        sa.Column("secondary_model", sa.String(200), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["provider_id"], ["user_api_providers.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("provider_id", "name", name="uq_user_api_profile_name"),
    )
    op.create_index("ix_user_api_profiles_user", "user_api_profiles", ["user_id", "is_default"])

    # 单个 AI 指向一条配置档（NULL = 不用配置档，走旧的三档回退）
    op.add_column("agents", sa.Column("llm_profile_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_agents_llm_profile", "agents", "user_api_profiles",
        ["llm_profile_id"], ["id"], ondelete="SET NULL",
    )
    op.add_column("agent_voice_configs", sa.Column("voice_profile_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_agent_voice_profile", "agent_voice_configs", "user_api_profiles",
        ["voice_profile_id"], ["id"], ondelete="SET NULL",
    )

    _migrate_global_config()


def _migrate_global_config() -> None:
    """把每个用户的旧全局 API 配置变成「厂商 + 默认配置档」

    单独成函数：这段只在有旧数据时才有意义，新库（users 为空）会整段跳过。
    预设一律取模块级的冻结快照，不问 app 里的活代码——迁移要的是"当年的内置清单"。
    """
    conn = op.get_bind()
    rows = conn.execute(sa.text(
        "SELECT id, api_base_url, api_key_encrypted, global_chat_model, global_work_model "
        "FROM users "
        "WHERE (api_base_url IS NOT NULL AND api_base_url <> '') "
        "   OR api_key_encrypted IS NOT NULL"
    )).fetchall()
    if not rows:
        return

    by_host: dict[str, dict] = {}
    for preset in _FROZEN_PRESETS:
        if preset["kind"] != "llm":
            continue
        host = _host_of(preset.get("base_url", ""))
        if host:
            by_host[host] = preset

    for user_id, base_url, key_enc, chat_model, work_model in rows:
        preset = by_host.get(_host_of(base_url or "")) if base_url else None
        name = preset["label"] if preset else "默认厂商"
        # 同一用户在同一家内置厂商上已经接过（理论上不会，保险）
        exists = conn.execute(sa.text(
            "SELECT 1 FROM user_api_providers WHERE user_id=:u AND kind='llm' AND name=:n"
        ), {"u": user_id, "n": name}).first()
        if exists:
            continue
        provider_id = conn.execute(sa.text(
            "INSERT INTO user_api_providers "
            "(user_id, kind, name, preset_key, base_url, default_api_key_encrypted, "
            " options, default_primary, default_secondary, thinking_supported, system_layout, sort_order) "
            "VALUES (:u, 'llm', :n, :pk, :url, :key, CAST(:opts AS JSONB), :p, :s, :think, :layout, 0) "
            "RETURNING id"
        ), {
            "u": user_id,
            "n": name,
            "pk": preset["key"] if preset else None,
            "url": base_url or (preset["base_url"] if preset else ""),
            "key": key_enc,
            # 模型清单存成 [{value,label}]，与界面同形；没有就留空让用户补
            "opts": json.dumps(_options_of(preset)) if preset else None,
            "p": chat_model or (preset.get("chat_model") if preset else None),
            "s": work_model or (preset.get("work_model") if preset else None),
            "think": bool(preset.get("thinking_supported")) if preset else False,
            "layout": preset.get("system_layout", "anywhere") if preset else "anywhere",
        }).scalar_one()
        conn.execute(sa.text(
            "INSERT INTO user_api_profiles "
            "(user_id, provider_id, name, api_key_encrypted, primary_model, secondary_model, "
            " is_default, sort_order) "
            "VALUES (:u, :p, '默认', NULL, NULL, NULL, true, 0)"
        ), {"u": user_id, "p": provider_id})


def _host_of(url: str) -> str:
    """只取主机名做匹配：中转站/带路径的地址也能对上同一家"""
    if not url:
        return ""
    rest = url.split("://", 1)[-1]
    return rest.split("/", 1)[0].split(":", 1)[0].lower()


def _options_of(preset: dict | None):
    if not preset:
        return None
    models = preset.get("models") or []
    return [{"value": m.get("value", ""), "label": m.get("label", m.get("value", ""))}
            for m in models] or None


def downgrade() -> None:
    op.drop_constraint("fk_agent_voice_profile", "agent_voice_configs", type_="foreignkey")
    op.drop_column("agent_voice_configs", "voice_profile_id")
    op.drop_constraint("fk_agents_llm_profile", "agents", type_="foreignkey")
    op.drop_column("agents", "llm_profile_id")
    op.drop_index("ix_user_api_profiles_user", table_name="user_api_profiles")
    op.drop_table("user_api_profiles")
    op.drop_index("ix_user_api_providers_user", table_name="user_api_providers")
    op.drop_table("user_api_providers")
