"""
AI 在线状态的唯一文案来源。

状态（active / dnd / inactive / blocked）是「我此刻在不在场」，不是「我能用哪些工具」：
它决定能不能主动社交，不决定请求里给不给工具定义（后者归能力版本链）。
两处必须说同一句话——提示词里的每轮读数，与执行闸拒绝时的回话——所以收在这里。
"""
from __future__ import annotations

STATE_LABELS: dict[str, str] = {
    "active": "在线",
    "dnd": "勿扰",
    "inactive": "离线",
    "blocked": "封禁",
}

# 每个状态对「能不能主动做事」的影响：只写事实，不给建议（平台工具的口径）
STATE_EFFECTS: dict[str, str] = {
    "active": "可以正常主动发言、私信、切换状态",
    "dnd": "只能被动响应：@你、私信、群公告能穿透，其余消息不主动开口",
    "inactive": "只有被 @ 才会醒",
    "blocked": "完全封禁，任何工具都用不了",
}


def state_label(state: str | None) -> str:
    key = str(state or "").strip()
    return STATE_LABELS.get(key, key or "未知")


def state_line(state: str | None) -> str:
    """给提示词的那一句（每轮读数，放尾部动态块，不占前缀）"""
    key = str(state or "").strip()
    effect = STATE_EFFECTS.get(key, "")
    label = state_label(key)
    return f"{label}（{effect}）" if effect else label


def state_denied_text(state: str | None, tool_name: str) -> str:
    """执行闸拒绝时的回话：说清是什么状态挡住了它，别让模型以为是工具坏了"""
    label = state_label(state)
    effect = STATE_EFFECTS.get(str(state or "").strip(), "")
    tail = f"；{effect}" if effect else ""
    return f"你当前是「{label}」，{tool_name} 这个工具现在不能用{tail}。要改状态用 switch_state。"
