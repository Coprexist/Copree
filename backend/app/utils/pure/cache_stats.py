"""
prompt cache 命中率的唯一口径 — 无 IO、无 DB 依赖。

**分母是 prompt_tokens，不是 total_tokens**：cached_tokens 是 prompt 的子集
（`prompt_tokens_details.cached_tokens`），拿 total 当分母会把 completion 也算进
「本该命中的量」，同一个账看起来低一截。设计文档 2.7 定的就是这个口径
（docs/group_world/design/group_world_design.md：命中率 = cached_tokens / prompt_tokens）。

世界用量、全站用量、对话用量三处都从这里取数：指标一旦各自四舍五入，面板之间就会
出现「都对不上」的小数差，查起来比没有还费劲。
"""
from __future__ import annotations

# 百分数保留一位小数：前端直接显示，不再各自 round
HIT_RATE_DIGITS = 1


def cache_hit_rate_pct(prompt_tokens: int | None, cached_tokens: int | None) -> float:
    """缓存命中率（百分数）。没有 prompt 的区间记 0.0 而不是 None：调用方要画曲线。"""
    prompt = int(prompt_tokens or 0)
    cached = int(cached_tokens or 0)
    if prompt <= 0:
        return 0.0
    return round(cached / prompt * 100, HIT_RATE_DIGITS)
