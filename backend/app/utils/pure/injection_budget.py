"""注入额度的分配 —— 无 IO、无 DB 依赖。

记忆与经历各自有上限，合起来还有一个总闸：两路都要落账本条目，一轮十条会把当轮上下文
明显撑鼓，而多数轮次其实凑不出十条真正相干的东西。额度先给记忆（既有机制先占），
剩下的才轮到经历；经历自己还有单价上限。
"""
from __future__ import annotations


def experience_slots(
    memories_used: int, *, memory_max: int, experience_max: int, total_max: int,
) -> int:
    """这一轮还能投几条经历。

    memories_used 是这一轮实际投出去 / 召回到的记忆条数（已按 memory_max 截过，再截一次
    没有害处：调用方把两件事分开算时口径仍然一致）。
    """
    used = max(0, min(int(memories_used), int(memory_max)))
    return max(0, min(int(experience_max), int(total_max) - used))
