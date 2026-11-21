"""工具结果带图：图片从工具结果里摘出来，另起一条附图消息注入。

钉三件事——工具消息本身不带字节（否则日志与 tool 角色都吃不消）、
标签按顺序念给模型、落库前字节换成占位。
"""
import pytest

from app.ai.executor import MAX_TOOL_IMAGES, _inject_tool_images, _take_tool_images
from app.utils.multimodal import IMAGE_PLACEHOLDER, image_note, strip_image_bytes

DATA_URL = "data:image/jpeg;base64,QUJD"


def test_images_are_taken_out_of_the_result_with_their_labels():
    result = {
        "ok": True,
        "frames": [{"画面": "00:20"}, {"画面": "00:30"}],
        "images": [{"url": DATA_URL, "label": "00:20"}, {"url": DATA_URL, "label": "00:30"}],
    }

    items = _take_tool_images(result, "bili_shot")

    assert "images" not in result, "字节从工具结果里摘出去"
    assert result["frames"], "文字标签留着"
    assert [it["label"] for it in items] == ["00:20", "00:30"]
    assert all(it["tool"] == "bili_shot" for it in items)


def test_plain_strings_are_allowed_and_non_images_dropped():
    result = {"images": ["https://example.com/a.jpg", "file:///etc/passwd", {"url": "data:image/png;base64,QQ"}]}

    items = _take_tool_images(result, "x")

    assert [it["url"] for it in items] == ["https://example.com/a.jpg", "data:image/png;base64,QQ"]
    assert [it["label"] for it in items] == ["", ""]


def test_result_without_images_is_untouched():
    result = {"ok": True}

    assert _take_tool_images(result, "x") == []
    assert result == {"ok": True}


def test_injection_is_one_user_message_plus_a_note():
    messages: list[dict] = [{"role": "tool", "tool_call_id": "1", "content": "{}"}]

    n = _inject_tool_images(messages, [
        {"url": DATA_URL, "label": "00:20", "tool": "bili_shot"},
        {"url": DATA_URL, "label": "00:30", "tool": "bili_shot"},
    ])

    assert n == 2
    assert [m["role"] for m in messages] == ["tool", "user", "system"]
    parts = messages[1]["content"]
    assert parts[0]["type"] == "text"
    assert "第1张 00:20" in parts[0]["text"] and "第2张 00:30" in parts[0]["text"]
    assert "不是用户发的" in parts[0]["text"]
    assert [p["type"] for p in parts[1:]] == ["image_url", "image_url"]
    assert messages[2]["content"] == image_note(2, source="工具刚返回的画面")


def test_injection_caps_the_image_count():
    messages: list[dict] = []

    n = _inject_tool_images(messages, [{"url": DATA_URL, "label": "", "tool": "x"}] * (MAX_TOOL_IMAGES + 3))

    assert n == MAX_TOOL_IMAGES
    assert len([p for p in messages[0]["content"] if p["type"] == "image_url"]) == MAX_TOOL_IMAGES


def test_log_sanitizer_replaces_bytes_but_keeps_text():
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": "这些是工具给的截图"},
            {"type": "image_url", "image_url": {"url": DATA_URL}},
        ],
    }]

    cleaned = strip_image_bytes(messages)

    assert [p["type"] for p in cleaned[0]["content"]] == ["text", "text"]
    assert cleaned[0]["content"][1]["text"] == IMAGE_PLACEHOLDER
    assert cleaned[0]["content"][0]["text"] == "这些是工具给的截图"
    assert messages[0]["content"][1]["type"] == "image_url", "原列表不被改动"


def test_log_sanitizer_leaves_plain_messages_alone():
    messages = [{"role": "user", "content": "纯文本"}, {"role": "system", "content": [{"type": "text", "text": "x"}]}]

    assert strip_image_bytes(messages) == messages
