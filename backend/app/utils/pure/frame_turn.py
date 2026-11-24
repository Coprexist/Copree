"""帧的轮生命周期：三态、额度、借贷、回归形态（docs/dev/frame_turn_lifecycle.md）。

纯函数、零 IO：帧就是 `agents.state_stack` 里的一个 dict，这里只改它的字段。
测试直接构造帧，不需要数据库。

三个词分清楚（§3.1）：
  * **step**  一次模型调用，额度的计量单位；
  * **message** 一条发出去的消息，是产物，一次 step 可以产出多条；
  * **帧**   一段情景（一个会话 / 一个世界 / 一次闹钟），额度挂在它身上。

防刷的核心（§4.4）：**刺激能续额度，切帧不能**。只有外部事件（人说话、通道消息、
闹钟到点、世界事件）调 `new_stimulus()`；主动切帧 / 被调度切回只改三态，不动额度。
"""
from __future__ import annotations

# ── 轮三态（§3.2）────────────────────────────────────────────────────
CLOSED = "closed"    # 没有未完成的轮
OPEN = "open"        # 有未完成的轮，被切走
RUNNING = "running"  # 正在执行
TURN_STATES = (CLOSED, OPEN, RUNNING)

DEFAULT_STEPS_LIMIT = 20
# I3：被借帧至少保留 1 步，只能由它自己消费（留给自己弹链收尾 / 写交接）
RESERVE_STEPS = 1


def ensure_fields(frame: dict) -> dict:
    """老帧没有这些字段：读时补默认（缺失 = closed / 0 步）。

    不写迁移：帧是 JSON 列，新增字段靠这里懒补齐，比给每个 AI 的每一帧跑一遍
    数据迁移便宜，也不会因为一份旧快照让新代码 KeyError。
    """
    frame.setdefault("turn_state", CLOSED)
    frame.setdefault("steps_used", 0)
    frame.setdefault("steps_limit", 0)
    frame.setdefault("lent_out", 0)
    frame.setdefault("borrow_from", [])
    if frame["turn_state"] not in TURN_STATES:
        frame["turn_state"] = CLOSED
    for key in ("steps_used", "steps_limit", "lent_out"):
        try:
            frame[key] = max(0, int(frame[key] or 0))
        except (TypeError, ValueError):
            frame[key] = 0
    if not isinstance(frame["borrow_from"], list):
        frame["borrow_from"] = []
    return frame


def steps_left(frame: dict) -> int:
    ensure_fields(frame)
    return max(0, frame["steps_limit"] - frame["steps_used"])


def new_stimulus(frame: dict, limit: int = DEFAULT_STEPS_LIMIT) -> dict:
    """一条**外部**刺激落在这一帧：额度重置（§4.1）。

    只允许外部事件调用它（人发的消息 / 通道消息 / 闹钟到点 / 世界事件 / 联邦消息）。
    系统自身的通知（能力变更、记忆变更、平台代销提示）不重置——否则 AI 靠内部一句话
    就能给自己续命，那就真成了自己刷额度。
    """
    ensure_fields(frame)
    frame["steps_limit"] = max(1, int(limit or DEFAULT_STEPS_LIMIT))
    frame["steps_used"] = 0
    frame["lent_out"] = 0
    frame["borrow_from"] = []
    frame["turn_state"] = RUNNING
    return frame


def suspend(frame: dict) -> dict:
    """切走但保留（形态 1）：running → open，额度不动。"""
    ensure_fields(frame)
    if frame["turn_state"] == RUNNING:
        frame["turn_state"] = OPEN
    return frame


def resume(frame: dict) -> dict:
    """无新刺激地切回（enter）：沿用 steps_used，只把状态置回 running（§4.1）。"""
    ensure_fields(frame)
    frame["turn_state"] = RUNNING
    return frame


def close(frame: dict) -> dict:
    """出链（end_turn / 额度耗尽）：剩余额度清零（§4.3 显式回收）。"""
    ensure_fields(frame)
    frame["steps_used"] = frame["steps_limit"]
    frame["turn_state"] = CLOSED
    return frame


def borrow(wanted: int, parent: dict) -> int:
    """向父帧借步数，返回实际借到多少（§4.2，I3 保留 1 步，I6 只向父帧）。

    **只转移，不创造**（I2）：父帧 steps_used 增加、lent_out 增加，子帧由调用方
    记 borrow_from——两笔相加为零。
    """
    if wanted <= 0:
        return 0
    ensure_fields(parent)
    available = steps_left(parent) - RESERVE_STEPS
    if available <= 0:
        return 0
    take = min(int(wanted), available)
    parent["steps_used"] += take
    parent["lent_out"] += take
    return take


def record_borrow(child: dict, parent: dict, n: int) -> None:
    """借入方记账：谁借的、借了多少（审计用，帧结束后不删）。"""
    ensure_fields(child)
    if n <= 0:
        return
    child["borrow_from"] = [
        *child["borrow_from"],
        {"frame": parent.get("id") or "", "n": int(n)},
    ]


def validate_regression(*, leave: str, middle: str, enter: bool) -> bool:
    """§5.3：合法回归形态恰三种，其余组合在语义上不成立。

    | leave   | middle | enter | 形态 |
    | suspend | keep   | 有    | 1 走开一会儿 |
    | end     | keep   | 有    | 2 这帧演完了 |
    | end     | end    | 有    | 3 整段收尾  |
    """
    if not enter:
        return False
    return (leave, middle) in {("suspend", "keep"), ("end", "keep"), ("end", "end")}
