"""图片占多少 token —— DeepSeek 官方视觉 token 计算器（v41 配置）的纯函数移植。

为什么要有它：图片注入不能按"张数"限制（那是 base64 时代拍脑袋的数），要按**字数上限**
判定——而字数上限的单位是 token。所以必须先把一张图折算成 token。

口径来源（不要自己发明）：
- 官方「Token & Token Usage」页的图片计算器，v41 配置；
- 与本机 DSH 的 `dsh-llm-deepseek/lib/index.js` 逐行对照过（2026-11-25），常量一致：
  patch 14px、3:1 下采样成 token cell、小图抬到 544×544、单图封顶 1024 token。

一句话换算（配合项目 estimate_tokens 的 1.5 字符/token）：
    一张图 ≈ 184–1024 token ≈ 276–1536 个汉字
小图（≤512²）几乎不要钱，贵的是大图和手机竖图。

纯函数，零 IO，不依赖 Pillow。
"""
from __future__ import annotations

PATCH_SIZE = 14
DOWNSAMPLE_RATIO = 3
MAX_IMAGE_TOKENS = 1024
MIN_PIXELS = 544 * 544
CELL_SIZE = PATCH_SIZE * DOWNSAMPLE_RATIO  # 42


def _int_div(value: int, divisor: int) -> int:
    return value // divisor


def _ceil_div(value: int, divisor: int) -> int:
    return (value + divisor - 1) // divisor


def _grid_tokens(grid_height: int, grid_width: int) -> int:
    """一格一 token；每行带一个分隔 token，另加首尾两个框 token。"""
    return grid_height * (grid_width + 1) + 2


def _grid_cells(padded_length: int) -> int:
    return _ceil_div(_int_div(padded_length, PATCH_SIZE), DOWNSAMPLE_RATIO)


def _solve_resize_ratio(height: int, width: int, budget: int) -> dict:
    """在 token 预算内解出保持宽高比的最大网格。"""
    aspect = height / width
    ideal_grid_width = ((budget - 2) / aspect + 0.25) ** 0.5 - 0.5
    ideal_grid_height = ideal_grid_width * aspect
    if ideal_grid_width < 1:
        solved_grid_width = 1
        solved_grid_height = _int_div(budget - 2, 2)
        best_width = solved_grid_width * CELL_SIZE
        best_height = solved_grid_height * CELL_SIZE
    elif ideal_grid_height < 1:
        solved_grid_height = 1
        best_width = (_int_div(budget - 2, solved_grid_height) - 1) * CELL_SIZE
        best_height = solved_grid_height * CELL_SIZE
    else:
        solved_grid_width = int(ideal_grid_width)
        solved_grid_height = int(ideal_grid_height)
        scale = min(solved_grid_width * CELL_SIZE / width,
                    solved_grid_height * CELL_SIZE / height)
        best_width = int(width * scale / PATCH_SIZE) * PATCH_SIZE
        best_height = int(height * scale / PATCH_SIZE) * PATCH_SIZE
    grid_height = _grid_cells(best_height)
    grid_width = _grid_cells(best_width)
    return {
        "grid_height": grid_height,
        "grid_width": grid_width,
        "best_width": best_width,
        "best_height": best_height,
        "num_tokens": _grid_tokens(grid_height, grid_width),
    }


def _safe_resize(height: int, width: int, padded_height: int, padded_width: int) -> dict:
    grid_height = _grid_cells(padded_height)
    grid_width = _grid_cells(padded_width)
    direct = {
        "grid_height": grid_height,
        "grid_width": grid_width,
        "best_width": padded_width,
        "best_height": padded_height,
        "num_tokens": _grid_tokens(grid_height, grid_width),
    }
    if direct["num_tokens"] <= MAX_IMAGE_TOKENS:
        return direct
    return _solve_resize_ratio(height, width, MAX_IMAGE_TOKENS)


def _resize_once(width: int, height: int) -> dict:
    scaled_width, scaled_height = width, height
    pixels = scaled_width * scaled_height
    if 0 < pixels < MIN_PIXELS:
        scale = (MIN_PIXELS / pixels) ** 0.5
        scaled_width = int(scaled_width * scale)
        scaled_height = int(scaled_height * scale)
    padded_width = _ceil_div(scaled_width, PATCH_SIZE) * PATCH_SIZE
    padded_height = _ceil_div(scaled_height, PATCH_SIZE) * PATCH_SIZE
    return _safe_resize(scaled_height, scaled_width, padded_height, padded_width)


def _same(a: dict, b: dict) -> bool:
    return (a["grid_height"], a["grid_width"], a["best_width"], a["best_height"],
            a["num_tokens"]) == (b["grid_height"], b["grid_width"], b["best_width"],
                                 b["best_height"], b["num_tokens"])


def image_tokens(width: int, height: int) -> int:
    """一张图在 DeepSeek 视觉接口里占多少 token（1–1024）。

    非正尺寸按 0 处理（没有尺寸就等于没有图，别让调用方到处判 None）。
    """
    if width <= 0 or height <= 0:
        return 0
    result = _resize_once(width, height)
    for _ in range(1, 10):
        nxt = _resize_once(result["best_width"], result["best_height"])
        if _same(nxt, result):
            return result["num_tokens"]
        result = nxt
    return result["num_tokens"]

# ── 跨厂商：只有 DeepSeek 能精确复刻，其余按「主流中位偏上」估 ──────────────
# 用户 2026-11-25 的口径：不要按封顶值算（封顶只在极端尺寸出现，拿它当每张图的价
# 会把预算压得太小），取"平均偏上"。
# 未知/本地部署一律按 DEFAULT_IMAGE_TOKENS——一张 1024 token ≈ 1536 字，够贵但不离谱，
# 且这个数字只影响"给几张图"，估高只是少给一张，估低才会超预算被折叠。
DEFAULT_IMAGE_TOKENS = 1024

# Anthropic：按像素 /750，单图封顶 1600（官方文档的 image tokens 口径）
ANTHROPIC_TOKENS_PER_PIXEL = 1 / 750
ANTHROPIC_MAX_TOKENS = 1600

# OpenAI：512px 切 tile，高细节 85 + 170/块（低细节固定 85，这里按高细节估）
OPENAI_BASE_TOKENS = 85
OPENAI_TOKENS_PER_TILE = 170
OPENAI_TILE_SIZE = 512


def provider_of(base_url: str | None, model: str | None = None) -> str:
    """这条通道大概是哪家：deepseek / anthropic / openai / other。"""
    url = (base_url or "").lower()
    name = (model or "").lower()
    if "deepseek" in url:
        return "deepseek"
    if "anthropic" in url or "claude" in name:
        return "anthropic"
    if "openai" in url:
        return "openai"
    return "other"


def estimate_image_tokens(width: int, height: int, provider: str = "other") -> int:
    """一张图占多少 token（按厂商分支；认不出用 DEFAULT_IMAGE_TOKENS）。

    只服务"字数预算"：不参与计费断言，也不追求和厂商逐 token 对齐。
    新增厂商 = 加一个分支，别把公式散到调用点。
    """
    if width <= 0 or height <= 0:
        return 0
    if provider == "deepseek":
        return image_tokens(width, height)
    if provider == "anthropic":
        return min(ANTHROPIC_MAX_TOKENS, int(width * height * ANTHROPIC_TOKENS_PER_PIXEL))
    if provider == "openai":
        tiles = ((width + OPENAI_TILE_SIZE - 1) // OPENAI_TILE_SIZE) * \
                ((height + OPENAI_TILE_SIZE - 1) // OPENAI_TILE_SIZE)
        return OPENAI_BASE_TOKENS + OPENAI_TOKENS_PER_TILE * tiles
    return DEFAULT_IMAGE_TOKENS

