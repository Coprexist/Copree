"""通道 system 摆放的判定 —— 保存时判一次，对话时只读结论。

守的是 2026-11 的报障：用户自己填了本机 llama-server 的地址，厂商行里躺着默认的
anywhere，于是每轮把 system 塞进对话中段，Qwen 的 Jinja 直接 500
（System message must be at the beginning）。

三件事各测一段：地址怎么猜、优先级谁高、运行时是否只读结论。
"""
from types import SimpleNamespace

import pytest

from app.utils.pure.chat_template import ANYWHERE, LEADING, guess_layout_from_host

pytestmark = pytest.mark.anyio

OWNER = 999102


def test_private_literal_addresses_guess_leading():
    """本机/内网字面量 → leading：本地推理服务的模板未必容忍中间 system。"""
    for url in (
        "http://localhost:8080",
        "http://127.0.0.1:11434/v1",
        "http://192.168.1.9:8080",
        "http://10.0.0.5:8000/v1",
        "http://172.20.3.4",
        "http://[::1]:8080",
        # Docker 固定的「本机」别名：Copree 在容器里、模型在宿主上（最常见自托管形态）
        "http://host.docker.internal:11434/v1",
        "http://gateway.docker.internal:8080",
    ):
        assert guess_layout_from_host(url) == LEADING, url


def test_public_or_unknown_hosts_stay_anywhere():
    """公网大厂与认不出的主机名都保持 anywhere：不把能跑的通道误判成严格档。"""
    for url in (
        "https://api.deepseek.com",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "http://gpu-box:8080",      # 内网主机名：只有探测能给出真结论
        "http://172.40.0.1:80",     # 不在私有段里
        "",
        None,
    ):
        assert guess_layout_from_host(url) == ANYWHERE, url


async def test_manual_choice_wins_over_preset_and_probe():
    from app.services.agent.channel_layout import decide_system_layout

    decision = await decide_system_layout(
        base_url="http://192.168.1.9:8080", manual="anywhere",
        preset_layout="leading", probe=False,
    )
    assert (decision.layout, decision.source) == (ANYWHERE, "user")


async def test_preset_is_used_when_nothing_else_is_known():
    from app.services.agent.channel_layout import decide_system_layout

    decision = await decide_system_layout(
        base_url="https://api.deepseek.com", preset_layout="leading", probe=False,
    )
    assert (decision.layout, decision.source) == ("leading", "preset")


async def test_without_any_evidence_it_guesses_from_the_address():
    from app.services.agent.channel_layout import decide_system_layout

    decision = await decide_system_layout(base_url="http://127.0.0.1:11434", probe=False)
    assert (decision.layout, decision.source) == (LEADING, "host")


def test_runtime_trusts_recorded_source_and_guesses_when_missing():
    """有来源就信存下来的结论；来源为空（历史行）才按地址猜。

    判定逻辑只有 provider_config.resolve_system_layout 一处；运行时通道信息
    走 channel_provider_info（与管理端预设同形），不再有第二份实现。
    """
    from app.utils.pure.provider_config import channel_provider_info

    def layout(stored, source, base_url):
        return channel_provider_info(
            {"system_layout": stored, "system_layout_source": source}, base_url,
        )["system_layout"]

    assert layout("none", "detected", "http://192.168.1.9:8080") == "none"
    # 来源为空 = 没有结论：内网地址保守判 leading
    assert layout("anywhere", None, "http://192.168.1.9:8080") == LEADING
    # 老行里存的 anywhere 不再被当结论：它只是当年的默认值
    assert layout("anywhere", None, "https://api.deepseek.com") == ANYWHERE


async def _seed(db):
    from sqlalchemy import text

    from db_reset import clear

    await clear(db, "user_api_profiles", "user_api_providers", "users")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES (:o, '主人', 'x', 'human')"
    ), {"o": OWNER})
    await db.commit()


def _me():
    return {"user_id": OWNER, "role": "user"}


async def test_self_hosted_llama_server_is_saved_as_leading(migrated_db):
    """#1 的回归：自建本机地址接入后不能是 anywhere，否则每轮 500。"""
    from app.database import async_session
    from app.routers.user_api_config import create_provider, list_providers, ProviderIn

    async with async_session() as db:
        await _seed(db)
        created = await create_provider(
            ProviderIn(kind="llm", name="本机 llama", base_url="http://192.168.1.9:8080"),
            _me(), db,
        )
        assert created["system_layout"] == LEADING
        assert created["system_layout_source"] == "host", "第一次保存只有地址这一条证据"

        row = (await list_providers("llm", _me(), db))["providers"][0]
        assert row["system_layout"] == LEADING, "列表里也必须是判定后的值，不能回到默认"


async def test_saved_provider_address_counts_as_registered(migrated_db):
    """厂商表里的地址也算「已保存过」：漏了它，自建地址永远不允许被探。"""
    from app.database import async_session
    from app.routers.user_api_config import create_provider, ProviderIn
    from app.services.agent.base_url_registry import saved_private_hosts

    async with async_session() as db:
        await _seed(db)
        await create_provider(
            ProviderIn(kind="llm", name="本机 llama", base_url="http://192.168.1.9:8080"),
            _me(), db,
        )
        hosts = await saved_private_hosts(db, OWNER)
        assert "192.168.1.9:8080" in hosts


async def test_public_preset_address_is_not_probed_again(migrated_db):
    """预设原地址直接信预设：不为每一家各等 5 秒探测。"""
    from app.database import async_session
    from app.routers.user_api_config import create_provider, ProviderIn
    from app.utils.pure.provider_presets import get_preset

    preset = get_preset("llama.cpp")   # 本地预设：leading
    async with async_session() as db:
        await _seed(db)
        created = await create_provider(
            ProviderIn(kind="llm", preset_key="llama.cpp"), _me(), db)
        assert created["system_layout"] == preset["system_layout"]
        assert created["system_layout_source"] == "preset"
async def test_reprobe_endpoint_records_the_detected_layout(migrated_db, monkeypatch):
    """用户点「重新识别」：结论直接落库，来源变成 detected。

    这条路径存在的理由：新建自建地址时它还不在「已保存」登记集里，后端不允许探私网，
    只能按地址猜；保存之后点一下重新识别，才是精确结论。
    """
    from app.database import async_session
    from app.routers.user_api_config import (
        create_provider, list_providers, probe_provider_layout, ProviderIn,
    )
    from app.services.agent import template_probe
    from app.services.agent.template_probe import TemplateProbe

    async def fake_probe(*args, **kwargs):
        return TemplateProbe(True, layout="leading", dialect="jinja", provider="llama.cpp",
                             template_hash="deadbeefdeadbeef", message="已识别：llama.cpp")

    async with async_session() as db:
        await _seed(db)
        created = await create_provider(
            ProviderIn(kind="llm", name="本机 llama", base_url="http://192.168.1.9:8080"),
            _me(), db,
        )
        assert created["system_layout_source"] == "host", "第一次只有地址这条证据"

        # 地址已经保存过，用户可以主动要求重新识别
        monkeypatch.setattr(template_probe, "probe_chat_template", fake_probe)
        result = await probe_provider_layout(created["id"], _me(), db)
        assert (result["layout"], result["source"]) == ("leading", "detected")

        row = (await list_providers("llm", _me(), db))["providers"][0]
        assert row["system_layout_source"] == "detected", "结论要落库，不能只在返回里"
        assert row["system_layout_checked_at"], "记下判定时间，界面上能看出是什么时候认的"