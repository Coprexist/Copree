"""语音（TTS）预设：与 LLM 预设同表不同类，靠 kind 分开

同一张 provider_presets 是为了让"配 API"只有一套组件；但谁都不能把对方当成自己——
语音预设没有 models/chat_model，LLM 预设没有音色，遍历方必须按 kind 过滤。
"""


def test_voice_presets_are_a_separate_group():
    from app.utils.pure.provider_presets import get_all_presets, get_voice_presets

    llm = get_all_presets()
    voice = get_voice_presets()
    assert all(p["kind"] == "llm" for p in llm)
    assert {p["key"] for p in voice} >= {"breeze", "minimax-cn", "minimax-intl"}
    assert not ({p["key"] for p in llm} & {p["key"] for p in voice}), "两类预设不能重名"

    for p in voice:
        assert p["models"] == [], "语音预设不该有模型清单"
        assert p["chat_model"] == "", "语音预设不该有聊天模型"
    for p in llm:
        assert p["voices"] == [], "LLM 预设不该有音色"


def test_voice_presets_never_ship_a_specific_voice_id():
    """预设不许代填音色：音色 id 是账号资源，只对创建它的那把 Key 有效

    2026-11-28：breeze 预设曾写死我们自己账号下的两条声线（voc_…）当默认音色，
    新建语音配置时不填就落库成它——别人用自己的 Key 接进来，第一次合成就上游 4xx，
    而且界面上还把我们私有的 id 当"默认模型"显示出来。
    """
    from app.utils.pure.provider_presets import get_voice_presets

    for p in get_voice_presets():
        assert p["voice_id"] == "", f"{p['key']} 预设不该代填音色 id（属于各账号，不通用）"
        assert p["voices"] == [], f"{p['key']} 预设不该内置私有音色清单"


def test_breeze_accepts_style_hint():
    from app.utils.pure.provider_presets import get_voice_presets

    breeze = next(p for p in get_voice_presets() if p["key"] == "breeze")
    assert breeze["style_hint"] is True, "Breeze 吃自由语气描述（instructions），AI 自己写怎么念"


def test_minimax_has_one_preset_per_region():
    """国内站与国际站互不相通：两条预设各自钉住一个 base_url，不自动猜站在哪边"""
    from app.utils.pure.provider_presets import get_voice_presets

    by_key = {p["key"]: p for p in get_voice_presets()}
    assert by_key["minimax-cn"]["base_url"] == "https://api.minimaxi.com/v1"
    assert by_key["minimax-intl"]["base_url"] == "https://api.minimax.io/v1"
    assert by_key["minimax-cn"]["style_hint"] is False, "MiniMax 的语气只有枚举"


def test_preset_list_defaults_to_llm_for_old_callers():
    """老调用方（模型下拉、api_probe、base_url 登记）不传 kind，拿到的必须只有 LLM"""
    from app.utils.pure.provider_presets import get_all_presets

    assert all(p["kind"] == "llm" for p in get_all_presets())
    both = get_all_presets("*")
    assert any(p["kind"] == "voice" for p in both) and len(both) > len(get_all_presets())
