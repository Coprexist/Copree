"""图片占多少 token、怎么并入字数预算

2026-11-25 定的口径：
- 图片不按「张数」限制（base64 时代的 max_images=1 已废）；
- 每张图按**厂商公式**折算成 token，再按 1.5 字/token 折成字数；
- 一条消息的「正文 + 图」共用字数上限（群设置 max_msg_display_len，默认 3840）；
- 没有字数上限的路径（私信 / 世界）不限制。

DeepSeek 那套公式必须与官方计算器一致（DSH 逐行对照过）；
其他厂商只求量级正确，未知一律 1024 token/图（中位偏上，不用封顶值）。
"""
import os
import tempfile

import pytest
from PIL import Image

from app.utils.pure.image_tokens import (
    DEFAULT_IMAGE_TOKENS,
    estimate_image_tokens,
    image_tokens,
    provider_of,
)
from app.utils.multimodal import image_display_chars, select_images_for_budget


def _write_png(data_dir: str, rel: str, width: int, height: int) -> dict:
    path = os.path.join(data_dir, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Image.new("RGB", (width, height), (30, 30, 30)).save(path)
    return {"path": rel, "mime_type": "image/png", "name": os.path.basename(rel)}


@pytest.mark.parametrize(("width", "height", "expected"), [
    (256, 256, 184),     # 小于 544² → 先抬到 544² 再进网格
    (512, 512, 184),
    (640, 480, 206),
    (800, 600, 317),
    (1024, 1024, 652),
    (1920, 1080, 968),
    (1080, 1920, 986),   # 手机竖图：比横图更贵
    (4096, 4096, 994),   # 超出上限按最大网格
])
def test_image_tokens_matches_published_grid(width, height, expected):
    assert image_tokens(width, height) == expected


def test_image_tokens_ignores_invalid_dimensions():
    assert image_tokens(0, 100) == 0
    assert image_tokens(-1, -1) == 0


def test_provider_of():
    assert provider_of("https://api.deepseek.com", "deepseek-flash") == "deepseek"
    assert provider_of("https://api.anthropic.com", "claude-sonnet") == "anthropic"
    assert provider_of("https://api.openai.com/v1", "gpt-4o") == "openai"
    assert provider_of("http://localhost:11434", "llava") == "other"


def test_unknown_provider_uses_median_not_cap():
    """认不出的通道取中位偏上（1024），不是 Anthropic 那个封顶 1600。"""
    small = estimate_image_tokens(512, 512, "other")
    large = estimate_image_tokens(4096, 4096, "other")
    assert small == large == DEFAULT_IMAGE_TOKENS == 1024


def test_provider_branches_share_one_entry():
    """同一张手机竖图：DeepSeek 精确、Anthropic 封顶、OpenAI 按 tile。"""
    assert estimate_image_tokens(1080, 1920, "deepseek") == 986
    assert estimate_image_tokens(1080, 1920, "anthropic") == 1600
    assert estimate_image_tokens(1080, 1920, "openai") == 2125


async def test_budget_keeps_newest_images_first():
    """预算放不下全部时：从**最新**往回保留，旧的降级成 [图片]。"""
    with tempfile.TemporaryDirectory() as data_dir:
        old = _write_png(data_dir, "attachments/old.png", 1024, 1024)   # 652 token ≈ 978 字
        new = _write_png(data_dir, "attachments/new.png", 1024, 1024)
        # 预算只够一张：旧的被丢
        kept = select_images_for_budget([old, new], data_dir, 1000, "deepseek")
        assert kept == [new]

        # 预算够两张：都给，且保持原顺序
        kept2 = select_images_for_budget([old, new], data_dir, 3000, "deepseek")
        assert kept2 == [old, new]


async def test_budget_none_means_unlimited():
    """没有字数上限判定的路径：有几张给几张。"""
    with tempfile.TemporaryDirectory() as data_dir:
        images = [_write_png(data_dir, f"attachments/{i}.png", 1024, 1024) for i in range(5)]
        assert select_images_for_budget(images, data_dir, None) == images


async def test_budget_always_keeps_at_least_one():
    """预算为 0 也至少给最新一张：一张都不给等于这次消息的图彻底丢了。"""
    with tempfile.TemporaryDirectory() as data_dir:
        old = _write_png(data_dir, "attachments/old.png", 1024, 1024)
        new = _write_png(data_dir, "attachments/new.png", 1024, 1024)
        assert select_images_for_budget([old, new], data_dir, 0, "deepseek") == [new]


async def test_image_display_chars_uses_provider_branch():
    with tempfile.TemporaryDirectory() as data_dir:
        att = _write_png(data_dir, "attachments/a.png", 1080, 1920)
        assert image_display_chars(att, data_dir, "deepseek") == int(986 * 1.5)
        assert image_display_chars(att, data_dir, "other") == int(1024 * 1.5)


async def test_missing_file_costs_nothing():
    """读不到尺寸的坏文件按 0 字算，不该把整条消息的额度吃掉。"""
    with tempfile.TemporaryDirectory() as data_dir:
        assert image_display_chars({"path": "attachments/不存在.png"}, data_dir) == 0
