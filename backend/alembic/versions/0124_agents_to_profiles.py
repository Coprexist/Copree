"""把每个 AI 现有的 API 配置也变成配置档：与兜底一致的就复用，有差异的新建并去重

背景：0123 只把「账号级全局配置」迁成了兜底配置档，AI 身上那套（agents.api_base_url /
api_key_encrypted / chat_model / work_model）还留在原地。UI 现在只有配置档一条路，
这些字段就变成了"用户改不到、但运行时仍会读"的僵尸配置。这一步把它们收编：

  1. AI 什么都没自填        → 直接指向兜底配置档（整条生效）
  2. AI 自填了、但算出来跟兜底一样 → 同样指向兜底档（不为等价配置多建一条）
  3. 算出来不一样            → 建一条配置档并指向它；同一用户内
                              (地址, Key, 聊天模型, 工作模型) 完全相同的组合只建一条（去重）

「算出来」的口径与运行时一致（services/agent/api_config_resolver.py）：AI 没填的字段
继承兜底档，而不是继承某个全局散字段。

兜底档不存在也要能迁：有的账号从来没配过全局（0123 就没给它建兜底档），但它的 AI 里
有人手填过 Key。这类用户单独兜一遍，兜底基准直接从 users 表那几个历史字段取。

Revision ID: 0124
Revises: 0123
"""
import json
from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0124"
down_revision: Union[str, None] = "0123"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 内置预设的冻结快照（与 0123 同一份）：迁移问的是"当年的内置清单"，不是今天的活代码
_FROZEN_PRESETS = json.loads(
    Path(__file__).with_name("_provider_presets_snapshot.json").read_text(encoding="utf-8")
)["presets"]


def upgrade() -> None:
    conn = op.get_bind()

    fallbacks: dict[int, dict] = {}
    for row in conn.execute(sa.text(
        "SELECT p.id AS profile_id, p.user_id, p.api_key_encrypted AS profile_key, "
        "       p.primary_model, p.secondary_model, "
        "       pr.id AS provider_id, pr.base_url, "
        "       pr.default_api_key_encrypted AS provider_key, "
        "       pr.default_primary, pr.default_secondary "
        "FROM user_api_profiles p JOIN user_api_providers pr ON pr.id = p.provider_id "
        "WHERE p.is_default = true AND pr.kind = 'llm'"
    )).fetchall():
        fallbacks[row.user_id] = {
            "profile_id": row.profile_id,
            "provider_id": row.provider_id,
            "base": row.base_url or "",
            "key": row.profile_key or row.provider_key,
            "chat": row.primary_model or row.default_primary,
            "work": row.secondary_model or row.default_secondary,
        }

    # 要处理的 owner：名下有"还没绑档"的 AI，且满足其一——
    #   这个 AI 自己填过东西（要给它建/复用一条档）
    #   这个 owner 有兜底档（什么都没填的 AI 也该指向兜底，界面上才看得出"当前用哪套"）
    owners = conn.execute(sa.text(
        "SELECT DISTINCT a.owner_id FROM agents a "
        "WHERE a.llm_profile_id IS NULL AND a.owner_id IS NOT NULL AND ("
        "  (a.api_base_url IS NOT NULL AND a.api_base_url <> '') OR "
        "  a.api_key_encrypted IS NOT NULL OR a.chat_model IS NOT NULL OR a.work_model IS NOT NULL "
        "  OR EXISTS (SELECT 1 FROM user_api_profiles p2 "
        "             JOIN user_api_providers pr2 ON pr2.id = p2.provider_id "
        "             WHERE p2.user_id = a.owner_id AND p2.is_default = true AND pr2.kind = 'llm'))"
    )).scalars().all()
    if not owners:
        return

    by_host: dict[str, dict] = {}
    for preset in _FROZEN_PRESETS:
        if preset["kind"] != "llm":
            continue
        host = _host(preset.get("base_url", ""))
        if host:
            by_host[host] = preset

    for user_id in owners:
        fb = fallbacks.get(user_id) or _fallback_from_user(conn, user_id)
        fallback_tuple = (fb["base"], fb["key"], fb["chat"], fb["work"])

        agents = conn.execute(sa.text(
            "SELECT id, name, api_base_url, api_key_encrypted, chat_model, work_model "
            "FROM agents WHERE owner_id = :u AND llm_profile_id IS NULL "
            "ORDER BY id"
        ), {"u": user_id}).fetchall()
        if not agents:
            continue

        created: dict[tuple, int] = {}
        serial = 0

        for agent in agents:
            if not (agent.api_base_url or agent.api_key_encrypted
                    or agent.chat_model or agent.work_model):
                if fb["profile_id"]:
                    _point(conn, agent.id, fb["profile_id"])
                continue

            t_base = agent.api_base_url or fb["base"]
            t_key = agent.api_key_encrypted or fb["key"]
            t_chat = agent.chat_model or fb["chat"]
            t_work = agent.work_model or fb["work"]
            target = (t_base, t_key, t_chat, t_work)

            if target == fallback_tuple and fb["profile_id"]:
                _point(conn, agent.id, fb["profile_id"])
                continue

            if target in created:
                _point(conn, agent.id, created[target])
                continue

            provider_id = _provider_for(conn, user_id, t_base, fb, by_host)
            provider_defaults = _provider_defaults(conn, provider_id)
            serial += 1
            profile_name = _unique_name(conn, provider_id, f"{agent.name} · 迁移配置")
            profile_id = conn.execute(sa.text(
                "INSERT INTO user_api_profiles "
                "(user_id, provider_id, name, api_key_encrypted, primary_model, "
                " secondary_model, is_default, sort_order) "
                "VALUES (:u, :p, :n, :k, :c, :w, false, :o) RETURNING id"
            ), {
                "u": user_id,
                "p": provider_id,
                "n": profile_name,
                "k": None if t_key == provider_defaults[0] else t_key,
                "c": None if t_chat == provider_defaults[1] else t_chat,
                "w": None if t_work == provider_defaults[2] else t_work,
                "o": serial,
            }).scalar_one()
            created[target] = profile_id
            _point(conn, agent.id, profile_id)


def _fallback_from_user(conn, user_id: int) -> dict:
    """没有兜底档的账号：回调它那几个历史字段，口径与 0123 迁移前一致"""
    row = conn.execute(sa.text(
        "SELECT api_base_url, api_key_encrypted, global_chat_model, global_work_model "
        "FROM users WHERE id = :u"
    ), {"u": user_id}).fetchone()
    return {
        "profile_id": None,
        "provider_id": None,
        "base": (row[0] if row else "") or "",
        "key": row[1] if row else None,
        "chat": row[2] if row else None,
        "work": row[3] if row else None,
    }


def _point(conn, agent_id: int, profile_id: int) -> None:
    conn.execute(sa.text(
        "UPDATE agents SET llm_profile_id = :p WHERE id = :a"
    ), {"p": profile_id, "a": agent_id})


def _host(url: str) -> str:
    if not url:
        return ""
    rest = url.split("://", 1)[-1]
    return rest.split("/", 1)[0].split(":", 1)[0].lower()


def _provider_defaults(conn, provider_id: int) -> tuple:
    row = conn.execute(sa.text(
        "SELECT default_api_key_encrypted, default_primary, default_secondary "
        "FROM user_api_providers WHERE id = :p"
    ), {"p": provider_id}).fetchone()
    return (row[0], row[1], row[2]) if row else (None, None, None)


def _provider_for(conn, user_id: int, base_url: str, fallback: dict, by_host: dict) -> int:
    """这套配置该挂在哪个厂商下：能复用就复用，不能就新建一条"""
    if fallback["provider_id"] and base_url == fallback["base"]:
        return fallback["provider_id"]

    existing = conn.execute(sa.text(
        "SELECT id FROM user_api_providers WHERE user_id = :u AND kind = 'llm' "
        "AND base_url = :b ORDER BY id LIMIT 1"
    ), {"u": user_id, "b": base_url}).first()
    if existing:
        return existing[0]

    preset = by_host.get(_host(base_url)) if base_url else None
    name = _unique_provider_name(conn, user_id, preset["label"] if preset else "自定义厂商")
    opts = None
    if preset:
        models = preset.get("models") or []
        opts = json.dumps([{"value": m.get("value", ""),
                            "label": m.get("label", m.get("value", ""))}
                           for m in models if m.get("value")]) or None
    return conn.execute(sa.text(
        "INSERT INTO user_api_providers "
        "(user_id, kind, name, preset_key, base_url, options, default_primary, "
        " default_secondary, thinking_supported, system_layout, sort_order) "
        "VALUES (:u, 'llm', :n, :pk, :b, CAST(:o AS JSONB), :p, :s, :t, :l, 100) RETURNING id"
    ), {
        "u": user_id,
        "n": name,
        "pk": preset["key"] if preset else None,
        "b": base_url,
        "o": opts,
        "p": (preset.get("chat_model") if preset else None),
        "s": (preset.get("work_model") if preset else None),
        "t": bool(preset.get("thinking_supported")) if preset else False,
        "l": preset.get("system_layout", "anywhere") if preset else "anywhere",
    }).scalar_one()


def _unique_provider_name(conn, user_id: int, base: str) -> str:
    name, i = base, 1
    while conn.execute(sa.text(
        "SELECT 1 FROM user_api_providers WHERE user_id = :u AND kind = 'llm' AND name = :n"
    ), {"u": user_id, "n": name}).first():
        i += 1
        name = f"{base} {i}"
    return name


def _unique_name(conn, provider_id: int, base: str) -> str:
    name, i = base, 1
    while conn.execute(sa.text(
        "SELECT 1 FROM user_api_profiles WHERE provider_id = :p AND name = :n"
    ), {"p": provider_id, "n": name}).first():
        i += 1
        name = f"{base} ({i})"
    return name


def downgrade() -> None:
    op.execute("UPDATE agents SET llm_profile_id = NULL")
    op.execute(
        "DELETE FROM user_api_profiles "
        "WHERE is_default = false AND name LIKE '%· 迁移配置%'"
    )
