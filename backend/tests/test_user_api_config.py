"""用户 API 配置的两层 CRUD 与删除约束

守的是三条界面承诺：
- 第一家配置档自动成为默认档（否则新用户没有默认可用）；
- 默认档不许直接删（否则所有没选配置档的 AI 一起换 Key，没人察觉）；
- 厂商名下还有档时，删它要显式 force。
"""
from sqlalchemy import text

from fastapi import HTTPException
import pytest

pytestmark = pytest.mark.anyio

OWNER = 999101


async def _seed(db):
    from db_reset import clear

    await clear(db, "user_api_profiles", "user_api_providers", "users")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES (:o, '主人', 'x', 'human')"
    ), {"o": OWNER})
    await db.commit()


def _me():
    return {"user_id": OWNER, "role": "user"}


async def test_two_layers_and_default_protection(migrated_db):
    from app.database import async_session
    from app.routers.user_api_config import (
        connect_presets, create_profile, delete_profile, delete_provider,
        list_providers, set_default_profile, update_profile, ConnectIn, ProfileIn,
    )

    async with async_session() as db:
        await _seed(db)

        # 接入一家内置厂商：字段从预设带出来，Key 留空
        await connect_presets(ConnectIn(kind="llm", preset_keys=["deepseek"]), _me(), db)
        listed = await list_providers("llm", _me(), db)
        assert len(listed["providers"]) == 1
        provider = listed["providers"][0]
        assert provider["name"] == "DeepSeek"
        assert provider["base_url"] and provider["has_key"] is False
        assert provider["options"], "模型清单要从预设带过来"
        pid = provider["id"]

        # 只在设置里接入，不会自动长出配置档
        assert provider["profiles"] == []

        # 第一条档自动成为默认
        first = await create_profile(ProfileIn(provider_id=pid, name="家里"), _me(), db)
        assert first["is_default"] is True
        second = await create_profile(ProfileIn(provider_id=pid, name="公司"), _me(), db)
        assert second["is_default"] is False

        # 设默认会互斥
        await set_default_profile(second["id"], _me(), db)
        listed = await list_providers("llm", _me(), db)
        flags = {p["name"]: p["is_default"] for p in listed["providers"][0]["profiles"]}
        assert flags == {"家里": False, "公司": True}

        # 默认档不能直接删
        with pytest.raises(HTTPException) as err:
            await delete_profile(second["id"], _me(), db)
        assert err.value.status_code == 409

        # 非默认档可以删
        await delete_profile(first["id"], _me(), db)

        # 名下还有档时不能删厂商
        with pytest.raises(HTTPException) as err:
            await delete_provider(pid, False, _me(), db)
        assert err.value.status_code == 409

        # force 才删
        await delete_provider(pid, True, _me(), db)
        assert (await list_providers("llm", _me(), db))["providers"] == []


async def test_key_only_returns_last4(migrated_db):
    from app.database import async_session
    from app.routers.user_api_config import (
        connect_presets, create_profile, list_providers, update_profile, ConnectIn, ProfileIn,
    )

    async with async_session() as db:
        await _seed(db)
        await connect_presets(ConnectIn(kind="llm", preset_keys=["deepseek"]), _me(), db)
        pid = (await list_providers("llm", _me(), db))["providers"][0]["id"]
        prof = await create_profile(ProfileIn(provider_id=pid, name="家里"), _me(), db)

        await update_profile(prof["id"], ProfileIn(api_key="sk-abcdef123456"), _me(), db)
        listed = await list_providers("llm", _me(), db)
        profile = listed["providers"][0]["profiles"][0]
        assert profile["has_key"] is True
        assert profile["key_last4"] == "3456"
        assert "sk-" not in str(listed), "接口不许回明文 Key"

        # 空串 = 清空覆盖，回到用厂商默认
        await update_profile(prof["id"], ProfileIn(api_key=""), _me(), db)
        listed = await list_providers("llm", _me(), db)
        assert listed["providers"][0]["profiles"][0]["has_key"] is False

async def test_provider_key_is_saved_and_cleared(migrated_db):
    """厂商自己的默认 Key 必须真落库。

    两张表的列名不同（厂商 default_api_key_encrypted / 配置档 api_key_encrypted），
    写错名字不会报错——SQLAlchemy 允许在实例上挂任意属性，只是那一次保存落不了库。
    表现就是「填了 Key、点保存、重开还是空」（2026-11-22 oxo 报）。
    """
    from app.database import async_session
    from app.routers.user_api_config import (
        create_provider, list_providers, update_provider, ProviderIn,
    )

    async with async_session() as db:
        await _seed(db)
        created = await create_provider(
            ProviderIn(kind="llm", name="自建", base_url="https://example.invalid",
                       api_key="sk-provider-9876"),
            _me(), db,
        )
        assert created["has_key"] is True, "新建厂商时填的 Key 必须落库"
        assert created["key_last4"] == "9876"
        assert "sk-provider-9876" not in str(created), "接口不许回明文 Key"

        pid = created["id"]
        await update_provider(pid, ProviderIn(api_key="sk-new-4321"), _me(), db)
        provider = (await list_providers("llm", _me(), db))["providers"][0]
        assert provider["has_key"] is True and provider["key_last4"] == "4321"

        # 不传 api_key = 不改：改个名字不该顺手把 Key 清掉
        await update_provider(pid, ProviderIn(name="改名"), _me(), db)
        assert (await list_providers("llm", _me(), db))["providers"][0]["has_key"] is True

        # 空串 = 清空
        await update_provider(pid, ProviderIn(api_key=""), _me(), db)
        assert (await list_providers("llm", _me(), db))["providers"][0]["has_key"] is False


async def test_preset_fields_are_filled_once_and_restorable(migrated_db):
    """预设 → 厂商行的映射只有一处：接入时按预设填，改乱后"恢复默认"能回到预设值。

    create / connect / restore 三个端点历史上有三份各写各的映射表：副模型、语音主模型、
    系统消息布局都只在其中一份里出现过，漏掉的那份就悄悄少一个字段。现在共用
    _preset_values，这里同时守"接入时填全"和"恢复时填回"。
    """
    from app.database import async_session
    from app.routers.user_api_config import (
        create_provider, list_providers, restore_provider, update_provider, ProviderIn,
    )
    from app.utils.pure.provider_presets import get_preset

    preset = get_preset("deepseek")
    async with async_session() as db:
        await _seed(db)

        # 接入：只给 key，其余字段从预设带出
        created = await create_provider(
            ProviderIn(kind="llm", preset_key="deepseek", api_key="sk-abc-9999"), _me(), db)
        assert created["name"] == preset["label"], "没填名字就沿用厂商名"
        assert created["base_url"] == preset["base_url"]
        assert created["default_primary"] == preset["chat_model"]
        assert created["default_secondary"] == preset["work_model"]
        assert created["system_layout"] == preset["system_layout"]
        assert created["thinking_supported"] is preset["thinking_supported"]
        assert [o["value"] for o in created["options"]] == [m["value"] for m in preset["models"]]

        # 用户改乱
        pid = created["id"]
        await update_provider(pid, ProviderIn(
            name="我的 DeepSeek", base_url="http://192.168.1.9:8080",
            default_primary="我自己填的", default_secondary="我自己填的副",
            system_layout="tail", thinking_supported=False,
        ), _me(), db)

        # 恢复默认：内容回预设，Key 与名字是用户的东西，不动
        restored = await restore_provider(pid, _me(), db)
        assert restored["base_url"] == preset["base_url"]
        assert restored["default_primary"] == preset["chat_model"]
        assert restored["default_secondary"] == preset["work_model"]
        assert restored["system_layout"] == preset["system_layout"]
        assert restored["thinking_supported"] is preset["thinking_supported"]
        assert restored["name"] == "我的 DeepSeek", "恢复默认不改厂商名"
        assert restored["has_key"] is True, "恢复默认不动 Key"

        # 本地 llama-server 的系统消息布局是 leading（它只认开头的 system）：
        # 这条曾因为 provider_bootstrap 自己拼字段而丢掉，接入路径必须带出来。
        local = await create_provider(ProviderIn(kind="llm", preset_key="llama.cpp"), _me(), db)
        assert local["system_layout"] == "leading"
        assert local["base_url"] == get_preset("llama.cpp")["base_url"]


async def test_voice_preset_primary_is_its_default_voice(migrated_db):
    """语音预设的主"模型"是默认音色：LLM 用 chat_model、语音用 voice_id，同一个位置"""
    from app.database import async_session
    from app.routers.user_api_config import connect_presets, list_providers, ConnectIn
    from app.utils.pure.provider_presets import get_preset

    preset = get_preset("breeze")
    async with async_session() as db:
        await _seed(db)
        await connect_presets(ConnectIn(kind="voice", preset_keys=["breeze"]), _me(), db)
        provider = (await list_providers("voice", _me(), db))["providers"][0]

        assert provider["kind"] == "voice"
        assert provider["default_primary"] == preset["voice_id"]
        assert provider["default_secondary"] is None, "语音没有副模型"
        assert [o["value"] for o in provider["options"]] == [v["value"] for v in preset["voices"]]


async def test_connect_rejects_preset_of_another_kind(migrated_db):
    """LLM 与语音不能互相接错：同一个 key 只能进自己那一类"""
    from app.database import async_session
    from app.routers.user_api_config import connect_presets, list_providers, ConnectIn

    async with async_session() as db:
        await _seed(db)
        with pytest.raises(HTTPException) as err:
            await connect_presets(ConnectIn(kind="llm", preset_keys=["breeze"]), _me(), db)
        assert err.value.status_code == 404
        assert (await list_providers("llm", _me(), db))["providers"] == []


async def test_probe_uses_only_this_provider_key(migrated_db, monkeypatch):
    """探测取 Key 只认这条厂商，不借用户账号级 Key。

    「Key 绝不跨厂商借」是 api_profiles.md 的铁律，探测这条路也一样：以前这里会回落到
    users.api_key_encrypted，于是拿 DeepSeek 的 Key 去打 ollama（看着能通，实际把两条
    配置混成一条）；而只用本地模型、没配过云端 Key 的用户会被 400「请先配置 API Key」
    挡在门外——本地引擎根本不需要 Key。
    """
    from db_reset import clear
    from app.database import async_session
    from app.models.user import User
    from app.routers.user import TestApiBody, test_api_connection
    from app.routers.user_api_config import (
        create_provider, update_provider, ProviderIn,
    )
    from app.services.agent import api_probe as probe_mod
    from app.utils.crypto import encrypt_api_key

    captured: dict = {}

    async def fake_probe(base_url, key, *, allow_private_hosts=()):
        captured["key"] = key
        return probe_mod.ProbeResult(ok=True, kind="ok", message="探针替身", models=("gemma4:26b",))

    monkeypatch.setattr(probe_mod, "probe_provider", fake_probe)

    async with async_session() as db:
        await clear(db, "user_api_providers", "users")
        owner = User(username="探测主人", password_hash="x", type="human",
                     api_key_encrypted=encrypt_api_key("sk-user-global"))
        db.add(owner)
        await db.flush()
        me = {"user_id": owner.id, "role": "admin"}
        provider = await create_provider(ProviderIn(kind="llm", preset_key="ollama"), me, db)
        await db.commit()

        body = TestApiBody(api_base_url=provider["base_url"], provider_id=provider["id"])
        res = await test_api_connection(body, me, db)
        assert res["ok"] is True and list(res["models"]) == ["gemma4:26b"]
        assert captured["key"] is None, "厂商没存 Key 时不许借用用户账号级 Key"

        # 这条厂商自己存了 Key → 用它
        await update_provider(provider["id"], ProviderIn(api_key="sk-ollama"), me, db)
        await db.commit()
        await test_api_connection(body, me, db)
        assert captured["key"] == "sk-ollama"
