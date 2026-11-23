"""把每个 AI 现有的语音配置也变成配置档：与 LLM 同一套两层结构

背景：语音（TTS）一直是「一个 AI 一套」（agent_voice_configs），而 LLM 已经改成
「厂商 + 配置档」两层。两边的数据形状本来就是同一套（kind 挂在厂商上、primary 是音色），
agent_voice_configs.voice_profile_id 也早已建好，只差把老数据搬过去：

  1. 每条有地址的语音配置 → 在这个 AI 主人名下建/复用一个 kind='voice' 的厂商
     （按 preset_key + base_url 认作同一家；默认 Key 取第一次见到的那把）
  2. 再建一条配置档（按 厂商 + Key + 音色 + 模型 去重），名字「<AI 名> · 迁移语音」
  3. 这条行的 voice_profile_id 指向它

行上的 provider / base_url / api_key / voice_id / model 一律不动：解析层在
「没选配置档」时仍然读它们，所以这一步可回滚——downgrade 只清指针与迁移出来的档。

Revision ID: 0125
Revises: 0124
"""
import json
from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0125"
down_revision: Union[str, None] = "0124"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 内置预设的冻结快照（与 0123 同一份）：迁移问的是"当年的内置清单"，不是今天的活代码
_FROZEN_PRESETS = json.loads(
    Path(__file__).with_name("_provider_presets_snapshot.json").read_text(encoding="utf-8")
)["presets"]
_FROZEN_BY_KEY = {p["key"]: p for p in _FROZEN_PRESETS}

TAG = "· 迁移语音"


def upgrade() -> None:
    conn = op.get_bind()

    rows = conn.execute(sa.text(
        "SELECT v.id, v.provider, v.base_url, v.api_key_encrypted, v.voice_id, v.model, "
        "       a.owner_id, a.name AS agent_name "
        "FROM agent_voice_configs v JOIN agents a ON a.id = v.agent_id "
        "WHERE v.voice_profile_id IS NULL "
        "  AND v.base_url IS NOT NULL AND v.base_url <> '' "
        "  AND a.owner_id IS NOT NULL"
    )).fetchall()
    if not rows:
        return

    providers: dict[tuple, int] = {}
    profiles: dict[tuple, int] = {}

    for row in rows:
        pkey = row.provider or ""
        voice = row.voice_id or ""
        model = row.model or ""
        key_enc = row.api_key_encrypted or None
        ident = (row.owner_id, pkey, row.base_url)
        provider_id = providers.get(ident)
        if provider_id is None:
            # 幂等：迁移集合指纹失效、手工 alembic upgrade 都会让这条迁移再跑一遍。
            # 同一家厂商（同用户 + 同根地址）已经建过就复用，别再插一行——
            # 插进去只会撞唯一约束被改名成「BreezeBlue 2」，界面上多一张空壳卡片。
            provider_id = _find_provider(conn, row.owner_id, row.base_url)
            if provider_id is None:
                preset = _FROZEN_BY_KEY.get(pkey) if pkey else None
                if preset is not None and preset.get("kind") != "voice":
                    preset = None
                label = (preset or {}).get("label") or pkey or "语音厂商"
                provider_id = _create_provider(
                    conn, row.owner_id, _unique_provider_name(conn, row.owner_id, label),
                    preset, pkey, row.base_url, key_enc, voice, model,
                )
            providers[ident] = provider_id

        pident = (provider_id, key_enc or "", voice, model)
        profile_id = profiles.get(pident)
        if profile_id is None:
            # 同理：同一条厂商下，音色与模型都一样的档就是同一个档，不重复建。
            profile_id = _find_profile(conn, provider_id, voice, model)
        if profile_id is None:
            provider_default_key = conn.execute(sa.text(
                "SELECT default_api_key_encrypted FROM user_api_providers WHERE id = :i"
            ), {"i": provider_id}).scalar_one()
            profile_id = _create_profile(
                conn, row.owner_id, provider_id,
                _unique_name(conn, provider_id, f"{row.agent_name or 'AI'}{TAG}"),
                None if provider_default_key == key_enc else key_enc,
                voice, model,
            )
        profiles[pident] = profile_id

        conn.execute(sa.text(
            "UPDATE agent_voice_configs SET voice_profile_id = :p WHERE id = :i"
        ), {"p": profile_id, "i": row.id})


def _create_provider(conn, user_id: int, name: str, preset, preset_key: str,
                     base_url: str, key_enc, voice: str, model: str) -> int:
    voices = (preset or {}).get("voices")
    if voices is None and voice:
        voices = [{"value": voice, "label": voice}]
    opts = json.dumps(voices or [], ensure_ascii=False)
    return conn.execute(sa.text(
        "INSERT INTO user_api_providers "
        "(user_id, kind, name, preset_key, base_url, default_api_key_encrypted, options, "
        " default_primary, default_secondary, thinking_supported, system_layout, sort_order) "
        "VALUES (:u, 'voice', :n, :pk, :b, :k, CAST(:o AS JSONB), :p, :s, false, 'anywhere', 100) "
        "RETURNING id"
    ), {
        "u": user_id, "n": name, "pk": preset_key or None, "b": base_url,
        "k": key_enc, "o": opts,
        "p": voice or (preset or {}).get("voice_id") or None,
        "s": model or None,
    }).scalar_one()


def _create_profile(conn, user_id: int, provider_id: int, name: str,
                    key_enc, voice: str, model: str) -> int:
    return conn.execute(sa.text(
        "INSERT INTO user_api_profiles "
        "(user_id, provider_id, name, api_key_encrypted, primary_model, secondary_model, "
        " is_default, sort_order) "
        "VALUES (:u, :pr, :n, :k, :p, :s, false, 100) RETURNING id"
    ), {
        "u": user_id, "pr": provider_id, "n": name, "k": key_enc,
        "p": voice or None, "s": model or None,
    }).scalar_one()


def _find_provider(conn, user_id: int, base_url: str) -> int | None:
    """这个用户名下已经接过同一根地址的语音厂商？有就返回它（幂等用）。

    按 base_url 认，不按 name：name 是展示名，用户随时会改；
    同一家厂商在同一个人名下本来就该只存在一条。
    """
    return conn.execute(sa.text(
        "SELECT id FROM user_api_providers "
        "WHERE user_id = :u AND kind = 'voice' AND base_url = :b "
        "ORDER BY id LIMIT 1"
    ), {"u": user_id, "b": base_url}).scalar_one_or_none()


def _find_profile(conn, provider_id: int, voice: str, model: str) -> int | None:
    """这条厂商下已经有一模一样的档（音色 + 副型号）？有就返回它。

    用 IS NOT DISTINCT FROM 而不是 =：这两列都可能是 NULL（没填副型号），
    而 NULL = NULL 在 SQL 里不成立，会漏判成"没有"进而重复插入。
    """
    return conn.execute(sa.text(
        "SELECT id FROM user_api_profiles "
        "WHERE provider_id = :p "
        "  AND primary_model IS NOT DISTINCT FROM :v "
        "  AND secondary_model IS NOT DISTINCT FROM :m "
        "ORDER BY id LIMIT 1"
    ), {"p": provider_id, "v": voice or None, "m": model or None}).scalar_one_or_none()


def _unique_provider_name(conn, user_id: int, base: str) -> str:
    name, i = base, 1
    while conn.execute(sa.text(
        "SELECT 1 FROM user_api_providers WHERE user_id = :u AND kind = 'voice' AND name = :n"
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
    op.execute("UPDATE agent_voice_configs SET voice_profile_id = NULL")
    op.execute(
        "DELETE FROM user_api_profiles WHERE is_default = false AND name LIKE '%{tag}%'".format(tag=TAG)
    )
