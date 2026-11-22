"""凭证解析的回退链 —— 重点守两条：

1. 配置档是整包的：选了它就不要再偷偷往上层借 Key 或模型；
2. **跨厂商不借 Key、不借模型名**：拿 DeepSeek 的 Key 去敲通义，只会得到一句
   供应商的 401，比报「这家还没填 Key」难查得多（2026-11-22 定下的规则）。
"""
from types import SimpleNamespace

from app.services.agent.api_config_resolver import (
    resolve_from,
    resolve_voice_from,
    same_provider,
)
from app.utils.crypto import encrypt_api_key

DEEPSEEK = "https://api.deepseek.com"
QWEN = "https://dashscope.aliyuncs.com/compatible-mode/v1"


def _agent(**kw):
    base = dict(api_base_url=None, api_key_encrypted=None, chat_model=None, work_model=None)
    base.update(kw)
    return SimpleNamespace(**base)


def _user(**kw):
    base = dict(api_base_url=None, api_key_encrypted=None,
                global_chat_model=None, global_work_model=None)
    base.update(kw)
    return SimpleNamespace(**base)


def _provider(**kw):
    base = dict(base_url=DEEPSEEK, default_api_key_encrypted=None,
                default_primary="provider-chat", default_secondary="provider-work",
                preset_key="deepseek", kind="llm", name="测试厂商",
                options=[], system_layout="anywhere", thinking_supported=False)
    base.update(kw)
    return SimpleNamespace(**base)


def _profile(**kw):
    base = dict(provider_id=1, api_key_encrypted=None,
                primary_model=None, secondary_model=None, user_id=1)
    base.update(kw)
    return SimpleNamespace(**base)


def test_same_provider_by_host():
    assert same_provider(DEEPSEEK, "https://api.deepseek.com/v1")
    assert not same_provider(DEEPSEEK, QWEN)
    assert not same_provider(None, DEEPSEEK)


def test_profile_is_whole_package():
    """选了配置档就用它的 Key / 模型，哪怕上层还有别家的 Key 也不借"""
    out = resolve_from(
        agent=_agent(api_base_url=QWEN, api_key_encrypted=encrypt_api_key("agent-key")),
        user=_user(api_key_encrypted=encrypt_api_key("user-key"), global_chat_model="user-chat"),
        profile=_profile(api_key_encrypted=encrypt_api_key("profile-key"),
                         primary_model="qwen3-max"),
        provider=_provider(base_url=QWEN),
        default_base_url=DEEPSEEK,
    )
    assert out.source == "profile"
    assert out.base_url == QWEN
    assert out.api_key == "profile-key"
    assert out.chat_model == "qwen3-max"
    # 副型号没填 → 厂商默认，不是上层的
    assert out.work_model == "provider-work"


def test_profile_without_key_falls_back_to_its_provider_only():
    out = resolve_from(
        profile=_profile(),
        provider=_provider(default_api_key_encrypted=encrypt_api_key("provider-key")),
        user=_user(api_key_encrypted=encrypt_api_key("user-key")),
    )
    assert out.api_key == "provider-key"
    assert out.chat_model == "provider-chat"


def test_profile_without_any_key_is_none_not_users_key():
    """配置档和它那家都没有 Key → 就是没有；绝不拿用户全局的 Key 顶上"""
    out = resolve_from(
        profile=_profile(),
        provider=_provider(),
        user=_user(api_key_encrypted=encrypt_api_key("user-key")),
    )
    assert out.api_key is None


def test_agent_own_address_blocks_borrowing():
    """AI 自己填了另一家的地址 → 不借全局的 Key 和模型"""
    out = resolve_from(
        agent=_agent(api_base_url=QWEN),
        user=_user(api_base_url=DEEPSEEK, api_key_encrypted=encrypt_api_key("user-key"),
                   global_chat_model="deepseek-chat"),
    )
    assert out.source == "agent"
    assert out.base_url == QWEN
    assert out.api_key is None
    assert out.chat_model is None


def test_agent_same_provider_borrows():
    out = resolve_from(
        agent=_agent(api_base_url=DEEPSEEK),
        user=_user(api_base_url="https://api.deepseek.com/v1",
                   api_key_encrypted=encrypt_api_key("user-key"),
                   global_chat_model="deepseek-chat"),
    )
    assert out.api_key == "user-key"
    assert out.chat_model == "deepseek-chat"


def test_agent_key_without_address_follows_global_address():
    """只填了 Key 没填地址 = 地址跟全局走，那就是同一家，Key 用它自己的"""
    out = resolve_from(
        agent=_agent(api_key_encrypted=encrypt_api_key("agent-key")),
        user=_user(api_base_url=DEEPSEEK, api_key_encrypted=encrypt_api_key("user-key")),
    )
    assert out.base_url == DEEPSEEK
    assert out.api_key == "agent-key"


def test_global_then_fallback():
    out = resolve_from(
        user=_user(api_base_url=QWEN, api_key_encrypted=encrypt_api_key("user-key")),
        default_base_url=DEEPSEEK,
    )
    assert out.source == "global"
    assert out.base_url == QWEN

    empty = resolve_from(default_base_url=DEEPSEEK)
    assert empty.source == "fallback"
    assert empty.base_url == DEEPSEEK
    assert empty.api_key is None


def test_voice_profile_shares_the_same_chain():
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
    out = resolve_voice_from(voice=voice, user=_user(api_key_encrypted=encrypt_api_key("other-key")))
    assert out.api_key is None

def test_provider_info_shape_matches_channel():
    """models 必须是模型 id 的字符串列表：resolve_model 靠 isinstance(str) 过滤，
    形状错了它就静默不约束模型名（把 A 家的模型发到 B 家的地址上，每轮 400）。"""
    from app.services.agent.api_config_resolver import provider_info_of
    from app.utils.pure.prompting import resolve_model

    provider = _provider(
        base_url=QWEN,
        options=[{"value": "qwen3-max", "label": "通义 Max"},
                 {"value": "qwen3-flash", "label": "通义 Flash"}],
    )
    resolved = resolve_from(profile=_profile(primary_model="qwen3-flash"), provider=provider,
                            default_base_url=DEEPSEEK)
    info = provider_info_of(provider, resolved)

    assert info["models"] == ["qwen3-max", "qwen3-flash"]
    assert info["default_model"] == "qwen3-flash"
    assert info["system_layout"] == "anywhere"

    # 这个 AI 记着 deepseek 的模型，但通道已经换成通义 → 必须落到通义的清单里
    assert resolve_model(_agent(chat_model="deepseek-flash"), provider_info=info) == "qwen3-flash"
    # 通道认识它记的模型时，仍然尊重 AI 自己的选择
    assert resolve_model(_agent(chat_model="qwen3-max"), provider_info=info) == "qwen3-max"

