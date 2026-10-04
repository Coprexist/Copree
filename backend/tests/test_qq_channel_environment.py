"""QQ 插件接入环境接口：只读内存（零新增网络往返），事实不再由 brief 重复第二遍。"""
import importlib.util
import time

import pytest

pytestmark = pytest.mark.anyio

PLUGIN_PATH = "/app/plugins/qq-channel/plugin.py"
_MOD = None


def _module():
    global _MOD
    if _MOD is None:
        spec = importlib.util.spec_from_file_location("qq_channel_env_probe", PLUGIN_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _MOD = mod
    return _MOD


def _plugin(group_map, facts, full_mode=None):
    p = _module().QqChannelPlugin()
    p._group_map = dict(group_map)
    p._group_facts = dict(facts)
    p._full_mode = (bool(full_mode), time.time()) if full_mode is not None else None
    return p


async def test_environment_maps_memory_facts_to_the_contract():
    p = _plugin(
        {"OPENID-A": 68},
        {"OPENID-A": {"name": "泰拉都市", "member_num": 27, "memo": "游戏群", "at": 1.0}},
        full_mode=True,
    )
    assert await p.environment(origin="group:68") == {
        "channel": "qq", "origin_name": "泰拉都市", "member_num": 27,
        "origin_memo": "游戏群", "full_mode": True,
    }


async def test_environment_never_hits_the_network():
    """零新增网络往返：把拉取口换成会炸的实现，取值仍须正常。"""
    p = _plugin({"OPENID-A": 68}, {"OPENID-A": {"name": "泰拉都市", "at": 1.0}})

    async def _boom(*args, **kwargs):
        raise AssertionError("environment() 不该发起拉取")

    p._group_facts_of = _boom
    assert (await p.environment(origin="group:68"))["origin_name"] == "泰拉都市"


async def test_environment_stays_silent_without_scope_or_facts():
    p = _plugin({"OPENID-A": 68}, {})
    assert await p.environment(origin="session:40_90") is None, "私信会话不是它的环境"
    assert await p.environment(origin="group:68") is None, "还没有群信息时不编造环境"


def test_brief_no_longer_repeats_the_channel_facts():
    """群名/人数/简介只由环境段讲一次；brief 里那行已删，防止再长回来。"""
    from app.services.plugin import channel as channel_mod

    assert not hasattr(channel_mod, "_qq_group_identity")
