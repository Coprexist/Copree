"""
插件环境上报 —— 校验、规范化、判定、渲染的唯一实现。

契约见 docs/plugin-dev/environment-api.md。三条不变式决定了这里只有一个分支：
- 判定只做等值比较、不认识字段语义，所以插件可以自由加维度而不改平台；
- text 在比较层是普通键（参与 canonical），在渲染层是覆盖键；
- 渲染只认公共键，私有键忽略——插件要说自己的话就写进 text。
"""
from __future__ import annotations

import json
from typing import Any, Callable

# 覆盖键：非空即整句采用，此时公共键不参与渲染（仍参与比较）
ENV_TEXT_KEY = "text"

# 公共键的渲染规则：新增一个公共键只加一行。平台只渲染这里的键，
# 其余键只参与比较——「平台不认识也能判定」正是插件可以自由扩维度的前提。
_ENV_RENDERERS: dict[str, Callable[[Any], str]] = {
    "channel": lambda v: f"你在 {v}",
    "origin_name": lambda v: f"「{v}」",
    "member_num": lambda v: f"{v} 人",
    "full_mode": lambda v: "全量模式" if v else "仅 @ 唤醒",
    # 简介是唯一可能偏长的字段：低频、且留在锁定段里可被缓存复用，但单字段仍要设上限
    "origin_memo": lambda v: f"简介：{str(v)[:ENV_MEMO_MAX]}",
}

# 单字段上限（简介这类）：环境段每轮进前缀，单个字段不该无界
ENV_MEMO_MAX = 80

# 平台认识的公共键清单（顺序即渲染顺序）
ENV_PUBLIC_KEYS: tuple[str, ...] = tuple(_ENV_RENDERERS)

# 既没有 text、也没有可用公共键时的兜底：AI 至少要知道「环境变了」
ENV_FALLBACK_TEXT = "你的环境有变化。"

# 渲染结果上限：环境段每轮进前缀，插件用 text 塞长文（群简介之类）会把每轮成本拉高。
# 这是防御闸，不是排版偏好——超长宁可截断，也不要让它进每一轮请求。
ENV_RENDER_MAX = 400


class EnvContractError(ValueError):
    """插件返回值违反契约 —— 平台丢弃本次上报，保持已告知值不变（不降级为无环境）"""


def normalize(value: Any) -> dict[str, Any] | None:
    """校验并归一插件返回值。None = 当前无环境；违规抛 EnvContractError。

    只收扁平基本类型：嵌套与浮点一律拒绝——判定和渲染都不解析它们，
    「看着支持」比「明确拒绝」更坑：作者会以为平台懂他的结构。
    """
    if value is None:
        return None
    if not isinstance(value, dict):
        raise EnvContractError(f"环境必须是 dict 或 None，收到 {type(value).__name__}")
    out: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key:
            raise EnvContractError(f"环境键必须是非空字符串，收到 {key!r}")
        # bool 是 int 的子类，先判它——两者都是合法值类型，分开只为可读
        if isinstance(item, (str, bool, int)):
            out[key] = item
            continue
        raise EnvContractError(
            f"环境值只收 str/int/bool，键 {key!r} 收到 {type(item).__name__}"
            "（列表或嵌套请自行序列化成字符串）"
        )
    return out


def canonical(value: dict[str, Any] | None) -> str:
    """可比较表示：先剔除空串 text（空串等同缺失），再排序序列化。

    排序保证键序不影响判定；ensure_ascii=False 让中文在日志里保持可读。
    """
    if value is None:
        return "null"
    trimmed = {k: v for k, v in value.items() if not (k == ENV_TEXT_KEY and v == "")}
    return json.dumps(trimmed, sort_keys=True, ensure_ascii=False)


def changed(previous: dict[str, Any] | None, current: dict[str, Any] | None) -> bool:
    """要不要通知：唯一一条判定，五种迁移（含有值↔None）都落在这里。

    新帧首次取值由调用方建立基线、不调用本函数——否则 AI 一进群就先收到一条假变更。
    """
    return canonical(previous) != canonical(current)


def render_environment(value: dict[str, Any] | None) -> str:
    """环境 → AI 看到的那句话：text 覆盖 → 公共键骨架 → 兜底，并统一加上限。"""
    if not value:
        return ENV_FALLBACK_TEXT
    text = value.get(ENV_TEXT_KEY)
    if isinstance(text, str) and text.strip():
        return _clip(text)
    parts = [render(value[k]) for k, render in _ENV_RENDERERS.items() if value.get(k) is not None]
    return _clip(" ".join(part for part in parts if part) or ENV_FALLBACK_TEXT)


def _clip(text: str) -> str:
    """截断到上限（见 ENV_RENDER_MAX）。"""
    if len(text) <= ENV_RENDER_MAX:
        return text
    return text[:ENV_RENDER_MAX] + "…"
