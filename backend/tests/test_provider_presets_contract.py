"""预设访问器契约 —— get_preset 与 get_all_presets 必须给同一份完整形状。

为什么值得一条测试：2026-11-23 插件商城保存 llama.cpp / lmstudio / vllm 预设必 500
（KeyError: work_model）。根因是两个访问器一个做归一化、一个不做，而消费方
（admin.py save_provider）按"字段齐全"直索引。归一化搬进访问器之后，这条测试守的是
「两个入口不会再次分叉」——包括未来新增的预设，不必再靠人记得在每个预设里把字段写全。
"""
from __future__ import annotations

import pytest

from app.utils.pure.provider_presets import PRESETS, get_all_presets, get_preset

# 消费方可以直接下标的字段（get_all_presets 历史上就返回这些）
CONTRACT_FIELDS = (
    "key", "label", "base_url", "api_key_url", "kind",
    "chat_model", "work_model", "embedding_model",
    "thinking_supported", "system_layout", "models",
    "global_default_chat_model", "global_default_work_model",
    "auto_config", "voice_id", "voices", "style_hint",
)


@pytest.mark.parametrize("key", sorted(PRESETS))
def test_preset_is_complete(key: str) -> None:
    preset = get_preset(key)
    assert preset is not None
    missing = [f for f in CONTRACT_FIELDS if f not in preset]
    assert missing == [], f"{key} 归一化后仍缺字段: {missing}"


def test_two_accessors_agree() -> None:
    """两个入口必须是同一个形状——分叉过一次就够了。"""
    by_key = {p["key"]: p for p in get_all_presets("*")}
    assert set(by_key) == set(PRESETS)
    for key, preset in by_key.items():
        assert preset == get_preset(key), key


def test_local_presets_are_indexable() -> None:
    """回归：这三家本地预设原先只写了 chat_model，直索引 work_model 就 500。"""
    for key in ("llama.cpp", "lmstudio", "vllm"):
        preset = get_preset(key)
        assert preset is not None
        assert preset["chat_model"] == ""
        assert preset["work_model"] == ""
        assert preset["embedding_model"] == ""
        assert preset["models"] == []
        assert isinstance(preset["thinking_supported"], bool)
        assert preset["system_layout"] in ("anywhere", "leading", "none")


def test_kind_filter_matches_contract() -> None:
    assert get_all_presets("llm")
    assert get_all_presets("voice")
    assert all(p["kind"] == "llm" for p in get_all_presets("llm"))
    assert all(p["kind"] == "voice" for p in get_all_presets("voice"))
    assert len(get_all_presets("*")) == len(PRESETS)


def test_unknown_preset_is_none() -> None:
    assert get_preset("no-such-provider") is None


# ── 构造器语义（build_provider_config 单一入口）─────────────────────

from app.utils.pure.provider_config import build_provider_config


def test_build_provider_config_fills_from_preset() -> None:
    cfg = build_provider_config(provider_key="deepseek")
    assert cfg["provider"] == "deepseek"
    assert cfg["name"] == "DeepSeek"
    assert cfg["base_url"]
    assert cfg["chat_model"]
    assert cfg["model_options"]


def test_build_provider_config_blank_override_keeps_preset() -> None:
    """表单留空 = 用预设（与历史 body.x or preset[x] 逐字节一致）"""
    base = build_provider_config(provider_key="deepseek")
    blank = build_provider_config(
        provider_key="deepseek",
        overrides={"chat_model": "", "base_url": None, "model_options": []},
    )
    assert blank == base


def test_build_provider_config_explicit_false_survives() -> None:
    """布尔用 None 表示「没给」；显式 False 不能被当成没给"""
    cfg = build_provider_config(provider_key="deepseek", overrides={"thinking_supported": False})
    assert cfg["thinking_supported"] is False


def test_build_provider_config_takes_preset_system_layout() -> None:
    """llama.cpp 预设是 leading：自动配置与保存时都不能被抹成 anywhere"""
    cfg = build_provider_config(provider_key="llama.cpp", overrides={"is_default": False})
    assert cfg["system_layout"] == "leading"


def test_manual_has_no_defaults() -> None:
    cfg = build_provider_config(
        provider_key="manual",
        overrides={"name": "自建中转", "base_url": "http://127.0.0.1:8080"},
    )
    assert cfg["provider"] == "manual"
    assert cfg["chat_model"] == "" and cfg["embedding_model"] == ""
    assert cfg["model_options"] == []
    assert cfg["system_layout"] == "anywhere"


def test_unknown_provider_key_has_no_defaults() -> None:
    cfg = build_provider_config(provider_key="no-such-provider")
    assert cfg["name"] == "" and cfg["base_url"] == "" and cfg["chat_model"] == ""

