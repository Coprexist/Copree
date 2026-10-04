"""LLM 返回的 usage 归一 — 唯一入口（无 IO、无 DB 依赖）。

各家接口把明细放在不同位置：命中数在 `prompt_tokens_details.cached_tokens`（工具轮）
或顶层 `cached_tokens`（首轮）、兼容接口还有 `prompt_cache_hit_tokens`；思考量在
`completion_tokens_details.reasoning_tokens`。落库与命中率只认顶层键，
所以每次拿到的原始 usage 都先过这里——漏归一的地方账是对的颗粒度、数却少一大截。
"""
from __future__ import annotations


def normalize_usage(raw: dict | None) -> dict:
    """把一次调用的原始 usage 归一成顶层键。

    空值/缺字段一律补齐为 0（调用方要能直接取键），明细子对象在归一后移除。
    """
    usage = dict(raw or {})
    prompt_details = usage.pop("prompt_tokens_details", None) or {}
    completion_details = usage.pop("completion_tokens_details", None) or {}

    cached = prompt_details.get("cached_tokens")
    if cached is None:
        cached = usage.get("cached_tokens")
    if cached is None:
        cached = usage.get("prompt_cache_hit_tokens")
    usage["cached_tokens"] = int(cached or 0)

    reasoning = completion_details.get("reasoning_tokens")
    if reasoning is None:
        reasoning = usage.get("reasoning_tokens")
    usage["reasoning_tokens"] = int(reasoning or 0)
    return usage
