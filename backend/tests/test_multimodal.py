"""附件到多模态 content 的转换（app/utils/multimodal.py）。纯函数，仅使用临时文件。

2026-09-13 的线上事故：image_attachments() 迭代了 normalize_attachments() 的返回值，
后者契约允许返回 None，导致不含附件的消息抛 TypeError。首条用例固定该契约。

不变量：
- 无可用图片时返回字符串，与不带图片的消息 payload 一致；
- 历史消息使用 [图片] 占位，仅最新一条携带字节。
"""
import base64
import contextlib
import os
import shutil
import tempfile

from app.utils.multimodal import (
    IMAGE_NOTE_PREFIX,
    IMAGE_PLACEHOLDER,
    VISION_UNSUPPORTED_HINT,
    build_content,
    image_attachments,
    image_note,
    image_placeholder,
    injected_image_count,
    is_image,
    is_vision_image,
    is_vision_unsupported_error,
    messages_have_images,
    strip_image_parts,
)

# 1x1 透明 PNG
PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
IMG = {"file_id": 1, "path": "a.png", "name": "a.png", "size": len(PNG_1PX), "mime_type": "image/png"}
TXT = {"file_id": 2, "path": "a.txt", "name": "a.txt", "size": 1, "mime_type": "text/plain"}


@contextlib.contextmanager
def _data_dir(files):
    """临时 data_dir，退出时删除。multimodal 将附件的相对路径拼在 data_dir 之下。"""
    root = tempfile.mkdtemp(prefix="multimodal-test-")
    try:
        for rel, blob in files.items():
            physical = os.path.join(root, rel)
            os.makedirs(os.path.dirname(physical), exist_ok=True)
            with open(physical, "wb") as fh:
                fh.write(blob)
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


# 退化输入

def test_image_attachments_never_raises_on_degenerate_input():
    """normalize_attachments 可能返回 None，此处必须兜住。"""
    for bad in (None, "坏了", "null", 0, [], ()):
        assert image_attachments(bad) == [], repr(bad)


def test_image_attachments_filters_non_images():
    assert image_attachments([IMG, TXT]) == [IMG]
    assert image_attachments("[]") == []
    assert image_attachments([TXT]) == []


def test_is_image_looks_at_mime_type_only():
    assert is_image({"mime_type": "image/png"})
    assert is_image({"mime_type": "IMAGE/JPEG"})
    assert not is_image({"mime_type": "text/plain"})
    assert not is_image({"name": "a.png"}), "仅依据 mime_type，不依据扩展名"


def test_placeholder_counts_images():
    assert image_placeholder(None) == ""
    assert image_placeholder([TXT]) == ""
    assert image_placeholder([IMG]) == IMAGE_PLACEHOLDER
    assert image_placeholder([IMG, {**IMG, "file_id": 9}]) == IMAGE_PLACEHOLDER + "×2"


# SVG：能渲染（image/*），但发不进视觉接口，也不能被历史占位吞掉

SVG = {"file_id": 3, "path": "a.svg", "name": "a.svg", "size": 10, "mime_type": "image/svg+xml"}


def test_svg_is_an_image_but_not_a_vision_image():
    assert is_image(SVG), "渲染语义：image/* 都算图片"
    assert not is_vision_image(SVG), "视觉接口只吃 png/jpeg/gif/webp"
    assert image_attachments([SVG]) == [], "SVG 不能进 build_content 的注入队列"


def test_svg_history_still_shows_placeholder():
    """发不过去不等于没发生过：历史里要留 [图片]，否则模型会装作看过。"""
    assert image_placeholder([SVG]) == IMAGE_PLACEHOLDER


# build_content

def test_build_content_is_a_plain_string_without_images():
    """无图片时返回字符串，而非单元素 parts 列表。"""
    assert build_content("你好", None, "/x") == "你好"
    assert build_content("你好", [TXT], "/x") == "你好"
    assert build_content(None, None, "/x") == ""
    assert build_content(None, [TXT], "/x") == ""


def test_build_content_injects_a_real_data_url():
    with _data_dir({"a.png": PNG_1PX}) as data_dir:
        parts = build_content("看图", (IMG,), data_dir)
    assert isinstance(parts, list), "有图片时应生成 parts 列表"
    assert parts[0] == {"type": "text", "text": "看图"}
    assert parts[1]["type"] == "image_url"
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_build_content_uses_placeholder_when_text_is_empty():
    with _data_dir({"a.png": PNG_1PX}) as data_dir:
        parts = build_content("", (IMG,), data_dir)
    assert parts[0] == {"type": "text", "text": IMAGE_PLACEHOLDER}


def test_build_content_falls_back_to_text_when_file_is_gone():
    """文件缺失时退回纯文本，避免生成残缺的多模态 content。"""
    assert build_content("看图", (IMG,), "/nonexistent-dir-xyz") == "看图"


def test_build_content_falls_back_when_oversized():
    with _data_dir({"a.png": PNG_1PX}) as data_dir:
        assert build_content("看图", (IMG,), data_dir, max_bytes=1) == "看图"


def test_build_content_declares_skipped_images():
    """单条默认只注入 1 张，其余须注明未提供，否则模型会引用不存在的图片。"""
    with _data_dir({"a.png": PNG_1PX, "b.png": PNG_1PX}) as data_dir:
        attachments = (IMG, {**IMG, "file_id": 2, "path": "b.png"})
        parts = build_content("看图", attachments, data_dir)
    assert injected_image_count(parts) == 1
    assert any("另有 1 张图片未提供" in p.get("text", "") for p in parts)


# 便签与视觉降级

def test_injected_image_count_only_counts_image_parts():
    assert injected_image_count("纯文本") == 0
    assert injected_image_count(None) == 0
    assert injected_image_count([{"type": "text", "text": "x"}]) == 0
    assert injected_image_count([{"type": "image_url", "image_url": {}}]) == 1


def test_image_note_states_the_platform_fact():
    note = image_note(2)
    assert note.startswith(IMAGE_NOTE_PREFIX)
    assert "2 张图片" in note


def test_strip_image_parts_removes_images_and_rewrites_the_note():
    """图片与便签须同时移除，否则会向纯文本模型声明其可查看图片。"""
    messages = [
        {"role": "system", "content": "静态前缀"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "这是什么"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}},
            ],
        },
        {"role": "system", "content": image_note(1)},
    ]
    degraded, removed = strip_image_parts(messages)

    assert removed == 1
    assert not messages_have_images(degraded)
    assert all(
        not (isinstance(m["content"], str) and m["content"].startswith(IMAGE_NOTE_PREFIX))
        for m in degraded
    ), "便签应被改写"
    assert VISION_UNSUPPORTED_HINT in str(degraded[1]["content"])
    assert "data:image" not in str(degraded), "图片字节应被移除"
    assert degraded[0] == messages[0], "无关消息保持不变"


def test_strip_image_parts_leaves_plain_messages_untouched():
    messages = [{"role": "user", "content": "纯文本"}, {"role": "system", "content": "x"}]
    degraded, removed = strip_image_parts(messages)
    assert removed == 0
    assert degraded == messages


def test_is_vision_unsupported_error_matches_real_wording():
    for text in (
        "400 Invalid image_url: this model does not support image input",
        "模型不支持图片输入",
        "unknown modality: image",
    ):
        assert is_vision_unsupported_error(text), text


def test_is_vision_unsupported_error_ignores_unrelated_failures():
    """宁漏勿误：401/404/429 不应被判定为模型不支持图片而触发降级。"""
    for text in ("401 Incorrect API key provided", "404 Not Found",
                 "429 rate limit exceeded", "", None):
        assert not is_vision_unsupported_error(text), repr(text)
