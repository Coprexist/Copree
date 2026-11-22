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
