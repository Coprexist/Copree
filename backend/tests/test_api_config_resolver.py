"""配置解析的三档链 —— 守四条：

1. **整条命中**：一档要么整套（地址 + Key + 模型）命中，要么整条跳过，没有跨档拼接；
2. **顺序固定**：① 绑定 → ② 池 → ③ 用户默认（池在默认之前，prefer_own_key 已删除）；
3. **不兜底**：三档都没命中就是失败，不再拿 settings.deepseek_base_url 假装配好了；
4. **本地模型不需要 Key**：判定按「这条通道需不需要 Key」，不是「有没有 Key」。
"""
from types import SimpleNamespace

from app.services.agent.api_config_resolver import (
    LEVEL_BOUND, LEVEL_DEFAULT, LEVEL_POOL, REASON_BAD_KEY, REASON_NO_BIND,
    REASON_NO_DEFAULT, REASON_NO_KEY, REASON_NO_QUOTA, REASON_OK, REASON_OWN_KEY_ONLY,
    Candidate, _profile_candidate, resolve, resolve_voice_from, same_provider,
)
from app.utils.crypto import encrypt_api_key
from app.utils.pure.prompting import resolve_model

DEEPSEEK = "https://api.deepseek.com"
QWEN = "https://dashscope.aliyuncs.com/compatible-mode/v1"
LOCAL = "http://localhost:8080/v1"


def _provider(**kw):
    base = dict(base_url=DEEPSEEK, default_api_key_encrypted=None,
                default_primary="provider-chat", default_secondary="provider-work",
                preset_key="deepseek", kind="llm", name="测试厂商",
                options=[], system_layout="anywhere", system_layout_source="preset",
                thinking_supported=False)
    base.update(kw)
    return SimpleNamespace(**base)


def _profile(**kw):
    base = dict(provider_id=1, api_key_encrypted=None, name="家里",
                primary_model=None, secondary_model=None, user_id=1)
    base.update(kw)
    return SimpleNamespace(**base)


def _cand(level, reason=REASON_OK, **kw):
    base = dict(level=level, reason=reason, label="", base_url=DEEPSEEK,
                api_key="k", chat_model="m", work_model="w",
                provider_info={"models": ["m"], "default_model": "m", "work_model": "w",
                               "thinking_supported": False, "system_layout": "anywhere"},
                models=("m",))
    base.update(kw)
    return Candidate(**base)


def test_same_provider_by_host():
    assert same_provider(DEEPSEEK, "https://api.deepseek.com/v1")
    assert not same_provider(DEEPSEEK, QWEN)
    assert not same_provider(None, DEEPSEEK)


def test_bound_hits_first_and_is_whole_package():
    """① 命中就不再往下看：池里有额度也不用"""
    out = resolve(bound=_cand(LEVEL_BOUND, base_url=QWEN, api_key="profile-key",
                              chat_model="qwen3-max", work_model=None),
                  pool=_cand(LEVEL_POOL, api_key="pool-key"),
                  default=_cand(LEVEL_DEFAULT, api_key="user-key"))
    assert out.source == LEVEL_BOUND
    assert out.credit_source == "profile"
    assert out.base_url == QWEN
    assert out.api_key == "profile-key"
    assert out.chat_model == "qwen3-max"
    # 副型号没填 → 厂商默认，不往下一档借
    assert out.work_model == "w"
    assert [s.status for s in out.chain] == ["hit"]


def test_pool_comes_before_default():
    out = resolve(bound=_cand(LEVEL_BOUND, REASON_NO_BIND),
                  pool=_cand(LEVEL_POOL, api_key="pool-key", pool_key_id=7),
                  default=_cand(LEVEL_DEFAULT, api_key="user-key"))
    assert out.source == LEVEL_POOL
    assert out.credit_source == "pool_key"
    assert out.pool_key_id == 7
    assert out.api_key == "pool-key"
    # ①② 两步都记进 chain：跳过的那步要能说出原因
    assert [s.level for s in out.chain] == [LEVEL_BOUND, LEVEL_POOL]
    assert out.chain[0].reason == REASON_NO_BIND


def test_default_is_the_last_resort():
    out = resolve(bound=_cand(LEVEL_BOUND, REASON_NO_BIND),
                  pool=_cand(LEVEL_POOL, REASON_NO_QUOTA),
                  default=_cand(LEVEL_DEFAULT, api_key="user-key"))
    assert out.source == LEVEL_DEFAULT
    assert out.credit_source == "user_key"
    assert out.chain[1].reason == REASON_NO_QUOTA


def test_no_tier_hit_is_a_failure_not_a_fake_default():
    """三档都空 → ④ 失败；不再拿 settings.deepseek_base_url 假装配好了"""
    out = resolve(bound=_cand(LEVEL_BOUND, REASON_NO_BIND),
                  pool=_cand(LEVEL_POOL, REASON_NO_QUOTA),
                  default=_cand(LEVEL_DEFAULT, REASON_NO_DEFAULT))
    assert out.source == "none"
    assert out.usable is False
    assert out.base_url == ""
    assert out.api_key is None
    assert out.chain[-1].level == "fail"
    assert out.missing_reason == REASON_NO_DEFAULT


def test_missing_reason_prefers_the_actionable_one():
    """③ 没配默认、① 的 Key 解不出来 → 先说「Key 坏了」，那才是用户能处理的那一步"""
    out = resolve(bound=_cand(LEVEL_BOUND, REASON_BAD_KEY),
                  pool=_cand(LEVEL_POOL, REASON_NO_QUOTA),
                  default=_cand(LEVEL_DEFAULT, REASON_NO_DEFAULT))
    assert out.missing_reason == REASON_BAD_KEY


def test_local_endpoint_needs_no_key():
    """本地 llama-server 不需要 Key：没 Key 也是命中（F4）"""
    c = _profile_candidate(LEVEL_BOUND, _profile(), _provider(base_url=LOCAL))
    assert c.reason == REASON_OK
    assert c.api_key is None
    out = resolve(bound=c, pool=_cand(LEVEL_POOL, REASON_NO_QUOTA),
                  default=_cand(LEVEL_DEFAULT, REASON_NO_DEFAULT))
    assert out.usable is True
    assert out.needs_key is False


def test_remote_endpoint_without_key_skips_this_tier():
    c = _profile_candidate(LEVEL_BOUND, _profile(), _provider())
    assert c.reason == REASON_NO_KEY
    assert c.hit is False


def test_bad_key_skips_and_records_the_source():
    c = _profile_candidate(
        LEVEL_BOUND, _profile(api_key_encrypted="not-a-valid-fernet-token"),
        _provider(default_api_key_encrypted=encrypt_api_key("provider-key")))
    assert c.reason == REASON_BAD_KEY
    assert c.decrypt_failures
    out = resolve(bound=c, pool=_cand(LEVEL_POOL, REASON_NO_QUOTA),
                  default=_cand(LEVEL_DEFAULT, api_key="user-key"))
    assert out.source == LEVEL_DEFAULT
    assert out.decrypt_failures


def test_own_key_only_mode_skips_pool():
    """force_own_key（私信「只用自己的」）→ ② 记录为 own_key_only，不查池"""
    out = resolve(bound=_cand(LEVEL_BOUND, REASON_NO_BIND),
                  pool=_cand(LEVEL_POOL, REASON_OWN_KEY_ONLY),
                  default=_cand(LEVEL_DEFAULT, api_key="user-key"))
    assert out.source == LEVEL_DEFAULT
    assert out.chain[1].reason == REASON_OWN_KEY_ONLY


def test_profile_key_falls_back_only_within_its_provider():
    """配置档没填 Key → 用它所属厂商的默认 Key；绝不跨厂商"""
    c = _profile_candidate(
        LEVEL_BOUND, _profile(),
        _provider(default_api_key_encrypted=encrypt_api_key("provider-key")))
    assert c.api_key == "provider-key"
    assert c.chat_model == "provider-chat"


def test_provider_info_shape_matches_channel():
    """models 必须是模型 id 的字符串列表：resolve_model 靠 isinstance(str) 过滤，
    形状错了它就静默不约束模型名（把 A 家的模型发到 B 家的地址上，每轮 400）。"""
    provider = _provider(
        base_url=QWEN,
        options=[{"value": "qwen3-max", "label": "通义 Max"},
                 {"value": "qwen3-flash", "label": "通义 Flash"}],
    )
    c = _profile_candidate(
        LEVEL_BOUND,
        _profile(primary_model="qwen3-flash", api_key_encrypted=encrypt_api_key("k")),
        provider)
    assert c.provider_info["models"] == ["qwen3-max", "qwen3-flash"]
    assert c.provider_info["default_model"] == "qwen3-flash"

    # 这个 AI 记着 deepseek 的模型，但通道已经换成通义 → 必须落到通义的清单里
    agent = SimpleNamespace(chat_model="deepseek-flash")
    assert resolve_model(agent, provider_info=c.provider_info) == "qwen3-flash"
    assert resolve_model(agent, provider_info=c.provider_info) == "qwen3-flash"


def test_voice_profile_shares_the_same_shape():
    voice = SimpleNamespace(base_url=None, api_key_encrypted=None, provider="",
                            voice_id="", model="", owner_user_id=1)
    out = resolve_voice_from(
        voice=voice,
        profile=_profile(api_key_encrypted=encrypt_api_key("voice-key")),
        provider=_provider(kind="voice", base_url="https://api.minimax.chat",
                           preset_key="minimax-cn", default_primary="voice-a"),
    )
    assert out.source == "profile"
    assert out.api_key == "voice-key"
    assert out.voice_id == "voice-a"
    assert out.provider == "minimax-cn"


def test_voice_does_not_borrow_across_providers():
    voice = SimpleNamespace(base_url="https://api.minimax.chat", api_key_encrypted=None,
                            provider="minimax-cn", voice_id="v1", model="",
                            owner_user_id=1)
    out = resolve_voice_from(voice=voice, user=SimpleNamespace(api_key_encrypted=encrypt_api_key("other-key")))
    assert out.api_key is None