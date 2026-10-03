"""模型别名与供应商配置对齐：与预设清单同源，别让库里那份副本停在旧代"""


def test_alias_targets_are_current_names():
    from app.services.agent.provider_presets import MODEL_ALIASES, PRESETS

    known = {m["value"] for p in PRESETS.values() for m in p["models"]}
    for old, new in MODEL_ALIASES.items():
        assert old not in known, f"{old} 已经改名，就不该再留在下拉列表里"
        assert new in known, f"{old} → {new}：目标不在任何预设的模型列表里"


def test_canonical_model_keeps_unknown_names():
    from app.services.agent.provider_presets import canonical_model

    assert canonical_model("deepseek-v4-flash") == "deepseek-flash"
    assert canonical_model("deepseek-flash") == "deepseek-flash"
    assert canonical_model("  deepseek-v4-flash  ") == "deepseek-flash"
    assert canonical_model("some-unknown-model") == "some-unknown-model"
    assert canonical_model(None) is None
    assert canonical_model("") == ""


def test_align_provider_models_follows_presets():
    """进了预设的条目整体对齐；手工条目只平升名字，自定义清单不许被动"""
    from app.utils.pure.provider_config import align_provider_models

    presets = {"deepseek": {
        "chat_model": "deepseek-flash",
        "work_model": "deepseek-v4-pro",
        "models": [{"value": "deepseek-flash", "label": "DeepSeek V4.1 Flash（快速）"}],
    }}
    items = [
        {"name": "DeepSeek", "provider": "deepseek", "base_url": "https://api.deepseek.com",
         "chat_model": "deepseek-v4-flash", "work_model": "deepseek-v4-pro",
         "model_options": [{"value": "deepseek-v4-flash", "label": "DeepSeek V4 Flash（快速）"}]},
        {"name": "我的中转", "provider": "manual", "base_url": "https://relay.example",
         "chat_model": "deepseek-v4-flash",
         "model_options": [{"value": "deepseek-v4-flash", "label": "旧标签"},
                           {"value": "custom-model", "label": "自留"}]},
    ]
    new, changed = align_provider_models(
        items, presets=presets, aliases={"deepseek-v4-flash": "deepseek-flash"},
    )
    deepseek, manual = new
    assert deepseek["chat_model"] == "deepseek-flash"
    assert deepseek["model_options"] == presets["deepseek"]["models"]
    assert deepseek["base_url"] == "https://api.deepseek.com", "使用者的 base_url 不归预设管"
    assert manual["chat_model"] == "deepseek-flash", "手工条目按别名平升名字"
    assert manual["model_options"][1] == {"value": "custom-model", "label": "自留"}
    assert changed == 4
    assert items[0]["chat_model"] == "deepseek-v4-flash", "原列表不动（纯函数）"
