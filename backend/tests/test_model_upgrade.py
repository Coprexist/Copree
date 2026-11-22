"""预设表 → 模型对齐表的收敛（app/services/agent/model_upgrade.py）。

2026-11-22 启动日志一直有一行 `旧模型名平升失败（不影响启动）: 'work_model'`：
upgrade_provider_config 用 `p["work_model"]` 取预设字段，但 ProviderPreset 里
work_model 是 NotRequired（有些档只有一个模型）。KeyError 让"把库里的模型清单对齐到
现役预设"这一步整段不执行——管理台与 resolve_model 看到的都是旧清单，
配置档里新选的模型因此被判成"清单里没有"（陈屿的 qwen3:4b 就是这样丢的）。
"""
from app.services.agent.model_upgrade import preset_models_for_alignment
from app.services.agent.provider_presets import PRESETS


def test_every_llm_preset_survives_the_mapping():
    llm = [p for p in PRESETS.values() if (p.get("kind") or "llm") == "llm"]
    assert llm, "预设表不应为空"
    mapped = preset_models_for_alignment(llm)
    assert len(mapped) == len(llm)
    for p in llm:
        entry = mapped[p["key"]]
        assert entry["chat_model"] == p.get("chat_model")
        assert entry["work_model"] == p.get("work_model")
        assert isinstance(entry["models"], list)


def test_preset_without_work_model_maps_to_none_instead_of_raising():
    only_chat = [{"key": "one-model", "chat_model": "m-1", "models": [{"value": "m-1", "label": "M1"}]}]
    assert preset_models_for_alignment(only_chat) == {
        "one-model": {"chat_model": "m-1", "work_model": None,
                      "models": [{"value": "m-1", "label": "M1"}]},
    }
