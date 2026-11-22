"""system 摆放能力位：解析入口与 payload 出口（纯函数 + 一个构造体）。

守两条：
1. `anywhere` 通道的 payload messages 必须是**同一个对象**（DeepSeek 前缀缓存逐字节不变）；
2. 存储只有一处 —— provider_config 条目的 `system_layout`，没存才回落预设。
"""
from app.ai.llm import _build_chat_payload
from app.utils.pure.provider_config import resolve_system_layout

MESSAGES = [
    {"role": "system", "content": "人格"},
    {"role": "system", "content": "帧清单"},
    {"role": "user", "content": "你好"},
    {"role": "system", "content": "【想起】记忆"},
]


# ---- 解析：只认一处存储 ---- #

def test_stored_layout_wins_over_preset():
    assert resolve_system_layout({"system_layout": "leading"}, "none") == "leading"


def test_preset_is_the_fallback():
    assert resolve_system_layout({}, "none") == "none"
    assert resolve_system_layout(None, "leading") == "leading"


def test_missing_everything_is_anywhere():
    assert resolve_system_layout({}, None) == "anywhere"
    assert resolve_system_layout(None, None) == "anywhere"


def test_garbage_stored_value_falls_back_to_preset():
    """脏值当没存：不能因为它把通道判成严格档。"""
    assert resolve_system_layout({"system_layout": "strict"}, "leading") == "leading"
    assert resolve_system_layout({"system_layout": ""}, None) == "anywhere"


# ---- payload 出口：唯一调用点 ---- #

def test_payload_anywhere_keeps_messages_identity():
    """anywhere 必须原对象 —— 这条通道今天怎么发，以后还怎么发。"""
    payload = _build_chat_payload(MESSAGES, "m", 0.8, 0.9, 128)
    assert payload["messages"] is MESSAGES


def test_payload_leading_demotes_dynamic_blocks():
    payload = _build_chat_payload(MESSAGES, "m", 0.8, 0.9, 128, system_layout="leading")
    roles = [m["role"] for m in payload["messages"]]
    assert roles == ["system", "user", "user"]
    assert payload["messages"][0]["content"] == "人格\n\n帧清单"


def test_payload_ignores_unknown_layout():
    payload = _build_chat_payload(MESSAGES, "m", 0.8, 0.9, 128, system_layout="wat")
    assert payload["messages"] is MESSAGES
