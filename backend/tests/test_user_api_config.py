"""用户 API 配置的两层 CRUD 与删除约束

守的是三条界面承诺：
- 新建的档一律不是默认（默认必须人点，后台不许替用户决定；语音尤其如此）；
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
    """两层结构与删除约束

    「默认档不许直接删」随 2026-11-28 的全局默认档取消一起删除：现在「用在哪」由
    场景序列表达，删一条档只会级联清掉引用它的序列格，不再需要「先选另一条默认」。
    厂商名下还有档时仍要显式 force——那条约束针对的是「别让人悄悄换了 Key」。
    """
    from app.database import async_session
    from app.routers.user_api_config import (
        connect_presets, create_profile, delete_profile, delete_provider,
        list_providers, update_profile, ConnectIn, ProfileIn,
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
        assert provider["profiles"] == [], "只在设置里接入，不会自动长出配置档"

        first = await create_profile(ProfileIn(provider_id=pid, name="家里"), _me(), db)
        assert first["is_default"] is False, "建档不替用户决定默认（该字段已 deprecated）"
        assert "kind_has_default" not in first, "不再需要「要不要设为默认」的信号"
        second = await create_profile(ProfileIn(provider_id=pid, name="公司"), _me(), db)
        assert second["is_default"] is False

        # 改名走部分更新
        await update_profile(first["id"], ProfileIn(name="家里-改名"), _me(), db)
        names = {p["name"] for p in (await list_providers("llm", _me(), db))["providers"][0]["profiles"]}
        assert names == {"家里-改名", "公司"}

        await delete_profile(first["id"], _me(), db)

        # 名下还有档时不能删厂商
        with pytest.raises(HTTPException) as err:
            await delete_provider(pid, False, _me(), db)
        assert err.value.status_code == 409

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


async def test_voice_preset_fills_nothing_private(migrated_db):
    """语音预设只带地址与 Key 入口：音色 id 是账号资源，接入时一律不代填

    2026-11-28：预设曾写死我们自己账号下的两条声线当默认音色，别人用自己的 Key
    接进来第一次合成就 4xx，界面上还把我们私有的 id 当"默认模型"显示出来。
    """
    from app.database import async_session
    from app.routers.user_api_config import connect_presets, list_providers, ConnectIn

    async with async_session() as db:
        await _seed(db)
        await connect_presets(ConnectIn(kind="voice", preset_keys=["breeze"]), _me(), db)
        provider = (await list_providers("voice", _me(), db))["providers"][0]

        assert provider["kind"] == "voice"
        assert provider["base_url"] == "https://api.breeze.blue/v1"
        assert provider["default_primary"] is None, "预设不代填音色（只在创建它的账号下有效）"
        assert provider["default_secondary"] is None, "语音没有副模型"
        assert not provider["options"], "预设不带私有音色清单"


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


async def test_scenarios_are_on_by_default_and_toggle(migrated_db):
    """场景总开关：没有记录就是开；关掉才留一行，开回来删行

    「默认全勾」是产品要求（2026-11-28）：用户不必先开启才能用默认配置。
    """
    from app.database import async_session
    from app.routers.user_api_config_scenarios import list_scenarios, set_scenario, ScenarioIn
    from app.utils.pure.api_scenarios import SCENARIO_KEYS

    async with async_session() as db:
        await _seed(db)

        listed = await list_scenarios(_me(), db)
        assert [s["key"] for s in listed["scenarios"]] == list(SCENARIO_KEYS)
        assert all(s["enabled"] for s in listed["scenarios"]), "第一次进来必须全开（没有记录 = 开）"
        assert all(s["routes"] == [] for s in listed["scenarios"]), "全新用户没有序列"

        await set_scenario("agent_llm", ScenarioIn(enabled=False), _me(), db)
        flags = {s["key"]: s["enabled"] for s in (await list_scenarios(_me(), db))["scenarios"]}
        assert flags["agent_llm"] is False
        assert flags["world_ai"] is True, "关一个不影响别的"

        await set_scenario("agent_llm", ScenarioIn(enabled=True), _me(), db)
        rows = (await db.execute(text(
            "SELECT count(*) FROM user_api_scenario_off WHERE user_id = :o"
        ), {"o": OWNER})).scalar()
        assert rows == 0, "开回来要删行：没有记录 = 开"

        with pytest.raises(HTTPException) as exc:
            await set_scenario("no_such_scene", ScenarioIn(enabled=False), _me(), db)
        assert exc.value.status_code == 404, "未知场景要报错，不能静默写进去"


async def test_scenario_routes_are_ordered_and_toggleable(migrated_db):
    """序列：追加、整体保存（顺序 + 子开关）、移除、当前优先格

    界面上的「当前优先使用」是序列里第一条启用的格，不是真实请求结果——
    真实结果由 AI 详情的 stepper 回答（同一份 Resolution）。
    """
    from app.database import async_session
    from app.routers.user_api_config import (
        ConnectIn, ProfileIn, connect_presets, create_profile, list_providers,
    )
    from app.routers.user_api_config_scenarios import (
        add_route, list_scenarios, remove_route, save_routes, RouteIn, RoutesIn,
    )

    async with async_session() as db:
        await _seed(db)
        await connect_presets(ConnectIn(kind="llm", preset_keys=["deepseek"]), _me(), db)
        pid = (await list_providers("llm", _me(), db))["providers"][0]["id"]
        a = await create_profile(ProfileIn(provider_id=pid, name="家里"), _me(), db)
        b = await create_profile(ProfileIn(provider_id=pid, name="公司"), _me(), db)

        await add_route("agent_llm", RouteIn(profile_id=a["id"]), _me(), db)
        await add_route("agent_llm", RouteIn(profile_id=b["id"]), _me(), db)
        rows = {s["key"]: s for s in (await list_scenarios(_me(), db))["scenarios"]}["agent_llm"]
        assert [r["profile_id"] for r in rows["routes"]] == [a["id"], b["id"]]
        assert [r["position"] for r in rows["routes"]] == [1, 2]
        assert rows["first_enabled_profile_id"] == a["id"]

        # 整体保存：倒序，并且关掉第二格
        await save_routes("agent_llm", RoutesIn(routes=[
            RouteIn(profile_id=b["id"], enabled=False),
            RouteIn(profile_id=a["id"]),
        ]), _me(), db)
        rows = {s["key"]: s for s in (await list_scenarios(_me(), db))["scenarios"]}["agent_llm"]
        assert [r["profile_id"] for r in rows["routes"]] == [b["id"], a["id"]]
        assert rows["routes"][0]["enabled"] is False, "子开关关掉要保留在序列里"
        assert rows["first_enabled_profile_id"] == a["id"], "关掉的格不算当前优先"

        await remove_route("agent_llm", b["id"], _me(), db)
        rows = {s["key"]: s for s in (await list_scenarios(_me(), db))["scenarios"]}["agent_llm"]
        assert [r["profile_id"] for r in rows["routes"]] == [a["id"]]

        # 序列只能挂自己的配置档：别人的档 id 一律 404，不允许跨账号
        with pytest.raises(HTTPException) as err:
            await add_route("agent_llm", RouteIn(profile_id=999999), _me(), db)
        assert err.value.status_code == 404


async def test_failed_route_sets_unread_until_read(migrated_db):
    """失败状态：红点未读、同格不重复发消息、成功不清未读、阅读才清"""
    from app.database import async_session
    from app.routers.user_api_config import (
        ConnectIn, ProfileIn, connect_presets, create_profile, list_providers,
    )
    from app.routers.user_api_config_scenarios import add_route, list_scenarios, RouteIn
    from app.services.agent.api_scenario_service import ack_status, record_attempt

    async with async_session() as db:
        await _seed(db)
        await connect_presets(ConnectIn(kind="llm", preset_keys=["deepseek"]), _me(), db)
        pid = (await list_providers("llm", _me(), db))["providers"][0]["id"]
        a = await create_profile(ProfileIn(provider_id=pid, name="家里"), _me(), db)
        await add_route("agent_llm", RouteIn(profile_id=a["id"]), _me(), db)

        assert await record_attempt(db, OWNER, "agent_llm", a["id"], ok=False, reason="no_key") is True
        assert await record_attempt(db, OWNER, "agent_llm", a["id"], ok=False, reason="no_key") is False, \
            "同一格连续失败只发一次消息"

        rows = {s["key"]: s for s in (await list_scenarios(_me(), db))["scenarios"]}["agent_llm"]
        assert rows["routes"][0]["status"]["unread"] is True
        assert rows["routes"][0]["status"]["reason"] == "no_key"

        # 成功一次：state 转 ok，但未读不自动清（红点只由阅读消除）
        await record_attempt(db, OWNER, "agent_llm", a["id"], ok=True)
        rows = {s["key"]: s for s in (await list_scenarios(_me(), db))["scenarios"]}["agent_llm"]
        assert rows["routes"][0]["status"]["state"] == "ok"
        assert rows["routes"][0]["status"]["unread"] is True

        await ack_status(db, OWNER, "agent_llm", a["id"])
        rows = {s["key"]: s for s in (await list_scenarios(_me(), db))["scenarios"]}["agent_llm"]
        assert rows["routes"][0]["status"]["unread"] is False


async def test_scenario_off_skips_the_whole_tier(migrated_db):
    """总开关关掉 → 第 ③ 格整档跳过，原因 scenario_off；探测调用不受影响"""
    from app.database import async_session
    from app.routers.user_api_config import (
        ConnectIn, ProfileIn, connect_presets, create_profile, list_providers,
    )
    from app.routers.user_api_config_scenarios import add_route, set_scenario, RouteIn, ScenarioIn
    from app.services.agent.api_config_resolver import (
        LEVEL_SCENARIO, REASON_NO_ROUTE, REASON_SCENARIO_OFF, resolve_user_llm,
    )

    async with async_session() as db:
        await _seed(db)
        await connect_presets(ConnectIn(kind="llm", preset_keys=["deepseek"]), _me(), db)
        pid = (await list_providers("llm", _me(), db))["providers"][0]["id"]
        profile = await create_profile(
            ProfileIn(provider_id=pid, name="家里", api_key="sk-test"), _me(), db,
        )
        await add_route("agent_llm", RouteIn(profile_id=profile["id"]), _me(), db)

        hit = await resolve_user_llm(db, OWNER, scenario="agent_llm")
        assert hit.source == LEVEL_SCENARIO, "场景开着且有可用格时该命中"

        await set_scenario("agent_llm", ScenarioIn(enabled=False), _me(), db)
        blocked = await resolve_user_llm(db, OWNER, scenario="agent_llm")
        assert blocked.source == "none"
        step = [s for s in blocked.chain if s.level == LEVEL_SCENARIO][0]
        assert step.reason == REASON_SCENARIO_OFF

        # 别的场景没配序列：给出 no_route，而不是偷偷借 agent_llm 的
        other = await resolve_user_llm(db, OWNER, scenario="world_ai")
        step = [s for s in other.chain if s.level == LEVEL_SCENARIO][0]
        assert step.reason == REASON_NO_ROUTE
        assert other.source == "none"

        # 探测类调用不传 scenario：链上没有第 ③ 格
        probe = await resolve_user_llm(db, OWNER)
        assert probe.source == "none"
        assert not [s for s in probe.chain if s.level == LEVEL_SCENARIO]
