"""模型名必须落在「这一轮真正走的通道」里

2026-10-19：用户给 AI 选了 xAI 的 grok-4.6（那是平台供应商预设里真实存在的一档），
但凭证解析落到 DeepSeek 的池 Key（api.deepseek.com）上，于是每一轮都 400：
"The supported API model names are deepseek-flash, deepseek-v4-pro, but you passed grok-4.6"。
模型名来自 AI 配置、地址来自凭证解析，中间从来没有一致性约束，用户那边什么都看不到。

现在通道信息（地址 + 它能用哪些模型 + 默认模型）由池 Key 自己记着（models/default_model），
没记才回到它关联的供应商预设；resolve_model 在通道给得出清单时把 AI 自选的模型收敛掉。
"""


def _agent(model: str | None, name: str = "Grok", agent_id: int = 52):
    from types import SimpleNamespace

    return SimpleNamespace(id=agent_id, name=name, chat_model=model)


def _channel(models: list[str], default: str | None = None, base: str = "https://api.deepseek.com"):
    from app.utils.pure.provider_config import channel_provider_info

    return channel_provider_info(
        {"name": "DeepSeek", "base_url": base, "chat_model": "deepseek-flash",
         "model_options": [{"label": "Flash", "value": "deepseek-flash"},
                           {"label": "Pro", "value": "deepseek-v4-pro"}]},
        base, models=models, default_model=default,
    )


def test_channel_info_prefers_its_own_model_list_over_the_preset():
    """池 Key 自己记了清单就用它记的；没记才回落到关联的供应商预设"""
    own = _channel(["relay-a", "relay-b"], default="relay-a")
    assert own["models"] == ["relay-a", "relay-b"] and own["default_model"] == "relay-a"

    from_preset = _channel([])
    assert from_preset["models"] == ["deepseek-flash", "deepseek-v4-pro"]
    assert from_preset["default_model"] == "deepseek-flash", "预设的 chat_model 就是这条通道的默认"


def test_model_outside_the_channel_falls_back_to_the_channel_default():
    from app.utils.pure.prompting import resolve_model

    channel = _channel([])
    # 这正是线上那次：grok-4.6 发给 DeepSeek
    assert resolve_model(_agent("grok-4.6"), provider_info=channel) == "deepseek-flash"
    # 通道认识就不动
    assert resolve_model(_agent("deepseek-v4-pro"), provider_info=channel) == "deepseek-v4-pro"
    # AI 没选模型 → 用通道默认
    assert resolve_model(_agent(None), provider_info=channel) == "deepseek-flash"


def test_unknown_channel_does_not_touch_the_choice():
    """通道给不出清单（自建中转没登记）时不约束模型名——否则没登记就用不了"""
    from app.utils.pure.prompting import resolve_model

    assert resolve_model(_agent("my-own-model"), provider_info={"models": []}) == "my-own-model"
    assert resolve_model(_agent("my-own-model"), provider_info=None) == "my-own-model"


def test_legacy_defaults_still_work():
    """老的调用口径（只有 global_default_model）不变"""
    from app.utils.pure.prompting import resolve_model

    assert resolve_model(_agent(None), global_default_model="deepseek-v4-pro") == "deepseek-v4-pro"
    assert resolve_model(_agent(None)) == "deepseek-flash"
