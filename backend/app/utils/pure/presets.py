"""
AI 配置预设合并逻辑（纯函数）— 无 IO，无副作用。
"""

# 预设档位顺序（用于判断升降级方向）
_PRESET_ORDER = {"chat": 0, "immersive": 1, "digital_life": 2}

# 强相关参数：切换预设时按升降级规则合并
_STRONG_NUMERIC_PARAMS = [
    "temperature", "top_p", "presence_penalty", "frequency_penalty",
    "max_tool_rounds", "alarm_max_tool_rounds", "max_alarms",
    "memory_recent_count",
]
_STRONG_BOOL_PARAMS = [
    "thinking_enabled", "force_alarm_on_end", "plan_injection_enabled", "is_ai_editable",
]
# 字符串枚举参数：切换预设时直接覆盖（不合并）
_STRONG_STRING_PARAMS = [
    "memory_load_mode", "memory_shared_scope",
]


def merge_preset_values(
    old_profile: str,
    new_profile: str,
    current_values: dict,
    preset_values: dict,
) -> tuple[dict, list[str]]:
    """
    按升降级规则合并预设值，返回 (合并后的值, 变更字段列表)。

    升级（chat→immersive→digital_life）：
      - 数值：max(当前, 预设) — 用户拉高的保留
      - 布尔：当前 OR 预设 — 任一开即开

    降级（逆向）：
      - 数值：min(当前, 预设) — 用户拉低的保留
      - 布尔：当前 AND 预设 — 都开才开
    """
    old_order = _PRESET_ORDER.get(old_profile, 0)
    new_order = _PRESET_ORDER.get(new_profile, 0)
    is_upgrade = new_order > old_order

    merged = {}
    changed: list[str] = []

    for key in _STRONG_NUMERIC_PARAMS:
        if key not in preset_values:
            continue
        cur = current_values.get(key)
        pre = preset_values[key]
        if cur is None:
            merged[key] = pre
            changed.append(key)
        elif is_upgrade:
            merged[key] = max(cur, pre)
            if merged[key] != cur:
                changed.append(key)
        else:
            merged[key] = min(cur, pre)
            if merged[key] != cur:
                changed.append(key)

    for key in _STRONG_BOOL_PARAMS:
        if key not in preset_values:
            continue
        cur = current_values.get(key)
        pre = preset_values[key]
        if cur is None:
            merged[key] = pre
            changed.append(key)
        elif is_upgrade:
            merged[key] = cur or pre
            if merged[key] != cur:
                changed.append(key)
        else:
            merged[key] = cur and pre
            if merged[key] != cur:
                changed.append(key)

    for key in _STRONG_STRING_PARAMS:
        if key not in preset_values:
            continue
        cur = current_values.get(key)
        pre = preset_values[key]
        if cur != pre:
            merged[key] = pre
            changed.append(key)

    return merged, changed
