"""外部通道的落点规则：通道侧的群/会话 → Copree 群。

收消息时（通道插件）决定"这条落到哪个群"，给 AI 讲通道规矩时（平台）要判断
"这个群接的是哪条通道"——同一件事的两面，规则只写在这里一处：
只看默认落点会漏掉"给单个通道群单独指定了落点"的那些群。

为什么不放插件里：插件是可选加载的，平台不该为了问一句"这个群接没接通道"而依赖它。
"""
from __future__ import annotations

import json
from typing import Any


SESSION_GROUP_PREFIX = "group:"


def session_ref(group_id: int) -> str:
    """群会话的会话标识（环境接口的 origin 用它）。

    与账本的会话键同形是刻意的：环境的作用域就是会话，两者必须指同一个东西。
    """
    return f"{SESSION_GROUP_PREFIX}{int(group_id)}"


def group_id_of_session(ref: str | int | None) -> int | None:
    """会话标识 → Copree 群号；不是群会话（私信、空值、格式不符）时返回 None。"""
    text = str(ref or "")
    if not text.startswith(SESSION_GROUP_PREFIX):
        return None
    try:
        return int(text[len(SESSION_GROUP_PREFIX):])
    except ValueError:
        return None


def parse_group_map(raw: Any) -> tuple[dict[str, int], list[str]]:
    """解析 group_map（通道侧群标识 → Copree 群 id）。

    坏项只丢那一项，把原因作为第二条返回（调用方决定记不记日志）：
    一处手写错误不该让整份映射失效，否则表现是"某个群突然不接消息了"。
    """
    if not raw:
        return {}, []
    try:
        data = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except Exception:
        return {}, ["不是合法 JSON，整份按空映射处理"]
    if not isinstance(data, dict):
        return {}, ["不是对象，整份按空映射处理"]
    out: dict[str, int] = {}
    problems: list[str] = []
    for key, value in data.items():
        try:
            out[str(key).strip()] = int(value)
        except (TypeError, ValueError):
            problems.append(f"群 {key} 的落点不是数字，已忽略该项")
    return out, problems


def landing_group(*, group_map: dict[str, int], default_group_id: int, origin: str) -> int:
    """这个通道侧群落到哪个 Copree 群：先查映射表，没有就落到实例的默认落点群"""
    return int(group_map.get(origin) or default_group_id or 0)


def serves_group(*, group_map: dict[str, int], default_group_id: int, group_id: int) -> bool:
    """这个通道实例接不接这个 Copree 群：默认落点，或映射表里任何一个群指向它"""
    if default_group_id and int(group_id) == int(default_group_id):
        return True
    return any(int(v or 0) == int(group_id) for v in group_map.values())
