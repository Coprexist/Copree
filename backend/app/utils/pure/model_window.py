"""模型 → 上下文窗口（纯函数，无 IO）

为什么要有它：`context_window` 以前是个全局常量（128K），不管用哪个模型——1M 的模型也按
128K×阈值触发，等于把大窗口白扔；而阈值算错不会报错，只会悄悄多花钱 / 早早压缩。
这里集中一张表：**认得出就用准的，认不出退回保守默认**——宁可早压（按小窗口），
也不要按大窗口放行到 API 报「context length exceeded」。

新模型加一行即可（先匹配到的赢）。
"""
from __future__ import annotations

# 认不出模型时的保守默认（DeepSeek 系列为 128K）
DEFAULT_CONTEXT_WINDOW = 128_000

# (模型名关键字, 窗口 tokens)：按顺序包含匹配
MODEL_CONTEXT_WINDOWS: tuple[tuple[str, int], ...] = (
    ("deepseek", 128_000),
    ("claude", 200_000),
    ("gemini", 1_000_000),
    ("gpt-4.1", 1_000_000),
    ("gpt-4o", 128_000),
)


def context_window_for(model: str | None) -> int:
    """按模型名取上下文窗口；认不出就用保守默认。"""
    name = (model or "").strip().lower()
    for key, window in MODEL_CONTEXT_WINDOWS:
        if key in name:
            return window
    return DEFAULT_CONTEXT_WINDOW