"""计划板 —— 纯函数，无 IO。

计划板是「这个状态下我排了什么」的一张快照，投递成账本条目（`kind=plan`）落在当轮新消息之前。

变没变不靠指纹、也不靠版本列：账本里该会话最后一条 plan 条目的正文**就是上次投出去的板子**，
这次渲染和它逐字节比即可（所以板子上只许出现绝对时间，不能出现「还有 5 分钟」这类相对量）。

归属只认两个事实，都在闹钟行上（单一来源）：`origin_context_ref`（谁拉起的）、`frame_id`（该唤醒谁）。
「该通知谁」不存——它就是这两者推出来的两类读者：本会话的、本会话排给别处的。
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.utils.pure.history import make_entry
from app.utils.pure.prompting import format_time_shanghai

PLAN_KIND = "plan"
PLAN_REF_PREFIX = "plan:"

PLAN_HEADER = "## ⏰ 计划与闹钟"
PLAN_FOOTER = ("（到点会在它所属的那个状态里叫醒你；计划有变就用 set_alarm / update_alarm / cancel_alarm "
               "把它改成现状，别让板子和事实对不上。）")
EMPTY_BOARD_TEXT = PLAN_HEADER + "\n这个状态名下已经没有任何计划了。"

PLAN_TASK_CHARS = 120     # 一条计划在板上占一行，超长截断
PLAN_FIRED_CHARS = 60     # 已执行的只是"它做过了"的回执，收得更紧
PLAN_FIRED_KEEP = 3       # 已执行的留几行：够看见"它执行了"，又不至于变成流水账

# 条目的头：首投与"改过"两种（比对正文时都要剥掉，否则每轮都会被判成"变了"）
PLAN_HEADS = ("【计划更新】以这份为准：\n", "【计划】\n")


def plan_ref(context_ref: str) -> str:
    """账本里的键：一段会话一份计划板。

    不用帧 id 当键——帧会被 pop/重建（新 uuid），拿它当键会让板子每轮都被当成"没见过"重投；
    会话轴稳定，正好是「这个状态下次被触发」的粒度。
    """
    return f"{PLAN_REF_PREFIX}{context_ref}"


def board_of(content: str) -> str:
    """从账本条目正文里取出板子本身（剥掉投递时的头），用于逐字节比对。"""
    text = content or ""
    for head in PLAN_HEADS:
        if text.startswith(head):
            return text[len(head):]
    return text


def make_plan_entry(context_ref: str, board: str, *, changed: bool = False) -> dict:
    """一条计划板的账本条目。changed=True 表示"上次投的那版作废，以这份为准"。"""
    head = PLAN_HEADS[0] if changed else PLAN_HEADS[1]
    return make_entry(PLAN_KIND, head + board, actor="system",
                      ref=plan_ref(context_ref), flags={"drop_on_unlock": True})


def plan_board(alarms, *, context_ref: str, current_frame_id: str = "",
               frames=None, was_delivered: bool = False) -> str:
    """渲染计划板；无内容可显示时返回空串（曾经投过则回一句"已经没有了"）。

    was_delivered：这个会话之前投过板子。没有计划时也得说一声，否则 AI 会以为旧计划还算数。
    """
    frames_by_id = {str(_get(f, "id") or ""): f for f in (frames or [])}
    mine, elsewhere, legacy, fired = [], [], [], []
    others: dict[str, int] = {}

    for alarm in alarms or []:
        status = str(_get(alarm, "status") or "pending")
        if status == "fired":
            # 回执只在本会话说：别的会话执行过的计划不该出现在这里；迁移前的老行没有归属，索性不报
            if str(_get(alarm, "origin_context_ref") or "") == context_ref:
                fired.append(alarm)
            continue
        if status == "cancelled":
            continue
        origin = str(_get(alarm, "origin_context_ref") or "")
        target = str(_get(alarm, "frame_id") or "")
        if origin and origin == context_ref:
            if target and target != current_frame_id:
                elsewhere.append(alarm)
            else:
                mine.append(alarm)
        elif target or origin:
            # 目标帧还在栈上就按帧归堆（能带上它的名字），否则退回会话键——会话键是稳的
            key = target if target in frames_by_id else (origin or target)
            others[key] = others.get(key, 0) + 1
        else:
            # 迁移前排的闹钟没有归属信息（两列都是空的）：不许猜成"这个会话的"
            legacy.append(alarm)

    if not (mine or elsewhere or others or legacy or fired):
        return EMPTY_BOARD_TEXT if was_delivered else ""

    lines = [PLAN_HEADER]
    if mine:
        lines.append("本会话：")
        lines += [_plan_line(a, frames_by_id) for a in _by_wake(mine)]
    if elsewhere:
        lines.append("排给别处（到点会在那个状态里叫醒你）：")
        lines += [_plan_line(a, frames_by_id, with_target=True) for a in _by_wake(elsewhere)]
    if others:
        parts = [f"{_frame_label(frames_by_id.get(key), key)} {count} 条"
                 for key, count in sorted(others.items())]
        lines.append(f"其他状态还有 {sum(others.values())} 条未到点：" + "、".join(parts))
    if legacy:
        ids = "、".join(f"#{_get(a, 'id')}" for a in _by_wake(legacy))
        lines.append(f"另有 {len(legacy)} 条早先排的闹钟没记归属：{ids}（拿不准就 list_alarms 看全量）")
    if fired:
        lines.append("已执行：")
        for alarm in _recent_fired(fired):
            lines.append(f"- ✅ {_wake_text(_get(alarm, 'fired_at') or _get(alarm, 'wake_at'))} "
                         f"{_task_text(_get(alarm, 'task'), PLAN_FIRED_CHARS)} #{_get(alarm, 'id')}")
    lines.append(PLAN_FOOTER)
    return "\n".join(lines)


# ── 渲染辅助 ──────────────────────────────────────────────────────

def _get(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _as_dt(value) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def _sort_key(alarm):
    return _as_dt(_get(alarm, "wake_at")) or datetime.max.replace(tzinfo=timezone.utc)


def _by_wake(alarms: list) -> list:
    return sorted(alarms, key=_sort_key)


def _recent_fired(alarms: list) -> list:
    return sorted(alarms, key=lambda a: _as_dt(_get(a, "fired_at")) or _as_dt(_get(a, "wake_at"))
                  or datetime.min.replace(tzinfo=timezone.utc), reverse=True)[:PLAN_FIRED_KEEP]


def _wake_text(value) -> str:
    """绝对时间（本地时区）——板子上不许出现相对量，否则每轮都"变"。"""
    dt = _as_dt(value)
    if dt is None:
        return str(value or "")
    return format_time_shanghai(dt.astimezone(timezone.utc).replace(tzinfo=None))


def _task_text(task, limit: int = PLAN_TASK_CHARS) -> str:
    text = " ".join(str(task or "").split())
    if len(text) > limit:
        text = text[:limit].rstrip() + "…"
    return text


def _frame_label(frame, key: str = "") -> str:
    """计数栏的标签：帧还在栈上就用它的身份，否则退回会话键（帧会被 pop，会话轴不会）。

    会话键两种口径：群是 `group:{id}`，私信就是 session_id（`40_90` 这种）——见 context_sync.context_ref。
    """
    if frame:
        type_ = _get(frame, "type") or "?"
        label = _get(frame, "label") or _get(frame, "doing") or ""
        return f"[{type_}]({label})" if label else f"[{type_}]"
    return f"其他会话({key})" if key else "已关闭的状态"


def _plan_line(alarm, frames_by_id: dict, *, with_target: bool = False) -> str:
    line = (f"- ({_wake_text(_get(alarm, 'wake_at'))}) {_task_text(_get(alarm, 'task'))} "
            f"#{_get(alarm, 'id')}")
    if with_target:
        line += " → " + _frame_label(frames_by_id.get(str(_get(alarm, "frame_id") or "")),
                                     str(_get(alarm, "frame_id") or ""))
    return line
