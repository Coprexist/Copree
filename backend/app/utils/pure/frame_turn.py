"""帧的轮生命周期：三态、额度、借贷、回归形态（docs/dev/frame_turn_lifecycle.md）。

纯函数、零 IO：帧就是 `agents.state_stack` 里的一个 dict，这里只改它的字段。
测试直接构造帧，不需要数据库。

三个词分清楚（§3.1）：
  * **step**  一次模型调用，额度的计量单位；
  * **message** 一条发出去的消息，是产物，一次 step 可以产出多条；
  * **帧**   一段情景（一个会话 / 一个世界 / 一次闹钟），额度挂在它身上。

额度**只存剩余步数**（`steps_left`），不存「已用 + 上限」两个数（2026-11-25 定）：
- 判断还有没有额度 = `steps_left > 0`，不用做减法；
- 刺激到达 = 赋一个数，耗尽 = 赋 0，借 = 两边加减——都是一行；
- 两个数会不同步（线上出现过 used=limit 但状态没落、帧卡死），单字段没有这个病。
旧帧的 `steps_used` / `steps_limit` 直接丢弃：**默认 0 = 没有额度**，下一次刺激会重新赋值。

防刷的核心（§4.4）：**刺激能续额度，切帧不能**。只有外部事件（人说话、通道消息、
闹钟到点、世界事件）调 `new_stimulus()`；主动切帧 / 被调度切回只改三态，不动额度。
"""
from __future__ import annotations

# ── 轮三态（§3.2）────────────────────────────────────────────────────
CLOSED = "closed"    # 没有未完成的轮
OPEN = "open"        # 有未完成的轮，被切走
RUNNING = "running"  # 正在执行
TURN_STATES = (CLOSED, OPEN, RUNNING)

# 一次刺激给多少步（可被 AI 的 max_tool_rounds 覆盖，见 state_stack_service._agent_step_limit）
DEFAULT_STEPS_LIMIT = 20
# I3：被借帧至少保留 1 步，只能由它自己消费（留给自己弹链收尾 / 写交接）
RESERVE_STEPS = 1

# 帧上唯一的额度字段：还剩多少步
STEPS_FIELD = "steps_left"


def ensure_fields(frame: dict) -> dict:
    """老帧没有这些字段：读时补默认（缺失 = closed / 0 步）。

    不写迁移：帧是 JSON 列，新增字段靠这里懒补齐，比给每个 AI 的每一帧跑一遍
    数据迁移便宜，也不会因为一份旧快照让新代码 KeyError。
    """
    frame.setdefault("turn_state", CLOSED)
    if STEPS_FIELD not in frame:
        # 老帧一次性换算：剩 = 上限 - 已用（两个都没有 = 新帧，0 步）。
        # **不能直接当 0**：重启时正在跑的帧要是被清零，它立刻就卡死了（正是这次要修的毛病）。
        try:
            old_limit = int(frame.get("steps_limit") or 0)
            old_used = int(frame.get("steps_used") or 0)
        except (TypeError, ValueError):
            old_limit, old_used = 0, 0
        frame[STEPS_FIELD] = max(0, old_limit - old_used)
    frame.setdefault(STEPS_FIELD, 0)
    frame.setdefault("lent_out", 0)
    frame.setdefault("borrow_from", [])
    # 旧口径的两个字段换算完就丢掉，之后只认 steps_left
    frame.pop("steps_used", None)
    frame.pop("steps_limit", None)
    if frame["turn_state"] not in TURN_STATES:
        frame["turn_state"] = CLOSED
    for key in (STEPS_FIELD, "lent_out"):
        try:
            frame[key] = max(0, int(frame[key] or 0))
        except (TypeError, ValueError):
            frame[key] = 0
    if not isinstance(frame["borrow_from"], list):
        frame["borrow_from"] = []
    return frame


def steps_left(frame: dict) -> int:
    """这一帧还剩几步（0 = 用完，得等下一次刺激）。"""
    ensure_fields(frame)
    return max(0, int(frame[STEPS_FIELD] or 0))


def new_stimulus(frame: dict, limit: int = DEFAULT_STEPS_LIMIT) -> dict:
    """一条**外部**刺激落在这一帧：额度重置（§4.1）。

    只允许外部事件调用它（人发的消息 / 通道消息 / 闹钟到点 / 世界事件 / 联邦消息）。
    系统自身的通知（能力变更、记忆变更、平台代销提示）不重置——否则 AI 靠内部一句话
    就能给自己续命，那就真成了自己刷额度。
    """
    ensure_fields(frame)
    frame[STEPS_FIELD] = max(1, int(limit or DEFAULT_STEPS_LIMIT))
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
    """无新刺激地切回（enter）：**沿用剩余步数**，只把状态置回 running（§4.1）。"""
    ensure_fields(frame)
    frame["turn_state"] = RUNNING
    return frame


def close(frame: dict, *, code: str = "", reason: str = "") -> dict:
    """出链（end_turn / 额度耗尽 / 异常中断）：剩余步数清零（§4.3 显式回收）。

    **同时把 status 置 ended**：is_running / current 读的是 status，只改 turn_state 的话
    这帧仍然算「在跑」、仍然占着 active——额度耗尽后它就永远卡在「当前帧但 0 额度」，
    别的会话的刺激进来也轮不到（2026-11-25 线上：私信帧 15/15 卡死，群消息全不回）。

    code / reason 记在帧上（ended_code / ended_reason）：「怎么没的」要能被下一个自己读到，
    被动结束（额度耗尽、异常、重启残留）尤其需要——主动 end_turn 时 AI 自己知道。
    """
    ensure_fields(frame)
    frame[STEPS_FIELD] = 0
    frame["turn_state"] = CLOSED
    frame["status"] = "ended"      # 与 state_stack.ENDED_STATUS 同值
    if code:
        frame["ended_code"] = code
    if reason:
        frame["ended_reason"] = reason
    return frame


def borrow(wanted: int, parent: dict) -> int:
    """向父帧借步数，返回实际借到多少（§4.2，I3 保留 1 步，I6 只向父帧）。

    **只转移、不创造**（I2）：父帧剩余步数减少、`lent_out` 增加，子帧由调用方
    把借到的加到自己的 `steps_left`——两笔相加为零。
    """
    if wanted <= 0:
        return 0
    ensure_fields(parent)
    available = steps_left(parent) - RESERVE_STEPS
    if available <= 0:
        return 0
    take = min(int(wanted), available)
    parent[STEPS_FIELD] -= take
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


def status_note(frame: dict, *, has_sent: bool = False) -> str:
    """刺激传导里的「你现在的状态」一段（§7.1）。

    模型默认所有消息都是实时的，不强调就会把两小时前的进度当成刚发生；
    所以这块必须带：在哪个作用域、本轮还剩几步、切走的后果。
    作用域之外的正文不进来（I5）——这里只有结构性事实。
    """
    ensure_fields(frame)
    label = frame.get("label") or frame.get("context_ref") or "这一段"
    lines = ["〔你现在的状态〕"]
    left = steps_left(frame)
    if left:
        lines.append(f"- 在：{label}，本轮还剩 {left} 步")
    else:
        lines.append(f"- 在：{label}")
    lines.append("- 本轮已经发过消息" if has_sent else "- 本轮尚未发出消息")
    lines.append("- 切走：本帧记为 open，可回来继续；额度保留")
    return chr(10).join(lines)
