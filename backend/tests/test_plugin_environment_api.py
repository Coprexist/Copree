"""插件环境上报契约：规范化、判定、渲染，以及插件侧默认实现与导出面。"""
import pytest

pytestmark = pytest.mark.anyio


def _raises(fn) -> bool:
    """本仓库测试跑在自带的极简 runner 上，没有 pytest.raises，手动判。"""
    try:
        fn()
    except ValueError:
        return True
    return False


def test_canonical_ignores_key_order():
    from app.utils.pure.plugin_env import canonical
    assert canonical({"a": 1, "b": 2}) == canonical({"b": 2, "a": 1})


def test_empty_text_is_the_same_as_missing_text():
    from app.utils.pure.plugin_env import canonical, changed
    assert canonical({"text": ""}) == canonical({})
    assert not changed({"text": ""}, {})


def test_text_participates_in_comparison():
    from app.utils.pure.plugin_env import changed
    assert changed({"text": "你在 QQ 群里"}, {"text": "你不在 QQ 群里"})


def test_five_transitions():
    from app.utils.pure.plugin_env import changed
    env = {"channel": "qq", "member_num": 27}
    assert not changed(dict(env), dict(env))
    assert changed(dict(env), {"channel": "qq", "member_num": 28})
    assert changed(dict(env), None)
    assert changed(None, dict(env))
    assert not changed(None, None)


def test_normalize_accepts_flat_basics_and_none():
    from app.utils.pure.plugin_env import normalize
    assert normalize(None) is None
    assert normalize({"channel": "qq", "member_num": 27, "full_mode": True}) == {
        "channel": "qq", "member_num": 27, "full_mode": True}


def test_normalize_rejects_what_it_cannot_compare():
    from app.utils.pure.plugin_env import normalize
    for bad in (1, "text", ["a"], {"k": 1.5}, {"k": None}, {"k": ["a"]}, {"k": {"n": 1}}, {"": "x"}):
        assert _raises(lambda b=bad: normalize(b)), f"应拒绝：{bad!r}"


def test_render_prefers_text_over_public_keys():
    from app.utils.pure.plugin_env import render_environment
    got = render_environment({"channel": "qq", "member_num": 27, "text": "你在泰拉都市群"})
    assert got == "你在泰拉都市群", got


def test_render_uses_public_keys_skeleton():
    from app.utils.pure.plugin_env import render_environment
    got = render_environment({"channel": "qq", "origin_name": "泰拉都市", "member_num": 27, "full_mode": True})
    assert got == "你在 qq 「泰拉都市」 27 人 全量模式", got


def test_render_is_length_capped():
    from app.utils.pure.plugin_env import ENV_RENDER_MAX, render_environment
    got = render_environment({"text": "长" * 1000})
    assert len(got) == ENV_RENDER_MAX + 1 and got.endswith("…"), len(got)


def test_render_falls_back_when_nothing_renderable():
    from app.utils.pure.plugin_env import ENV_FALLBACK_TEXT, render_environment
    assert render_environment({"rev": "a3f9"}) == ENV_FALLBACK_TEXT
    assert render_environment({}) == ENV_FALLBACK_TEXT
    assert render_environment(None) == ENV_FALLBACK_TEXT


async def test_plugin_base_has_no_environment_by_default():
    from app.services.plugin.api import ServicePlugin

    class _P(ServicePlugin):
        pass

    assert await _P().environment(origin="OPENID") is None


def test_public_api_exports_the_contract():
    from app.services.plugin.api import ENV_PUBLIC_KEYS, ENV_TEXT_KEY
    assert ENV_TEXT_KEY == "text"
    assert ENV_PUBLIC_KEYS == ("channel", "origin_name", "member_num", "full_mode", "origin_memo")
