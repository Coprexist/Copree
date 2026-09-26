"""跨状态便签纯函数 —— 无 IO、无 DB 依赖。

为什么单开这套（不复用工作区 TODO/PLAN/JOURNAL）：
- 工作区那三个文件是**状态内**的，跟着一段对话、一件事走；
- 真正需要跨状态传递的只有「临时且有时效」的留言（"群里有人问暗号就答 7788"）。
  长期该记住的东西归 AI 自己的提示词（update_self_config），不往便签里堆。

**投递制**（对齐 docs/dev/capability_lazy_loading.md 的锁）：
- 记录（agent 级）只管"还能不能投递"：写下后 NOTE_TTL_CALLS 次 API 调用内有效，
  过期就不再投给任何会话——这就是"清除过时便签"，比如 AI 一直在忙工作时不会再收到它；
- 一旦投进某个会话，就是**一次性过户**：内容抄进那段会话的上下文，此后归它的锁管，
  每轮字节完全相同地待在前缀里（缓存命中、不重复花 token），直到那段对话 compact/clear 解锁重建；
- 因此：记录侧事后无论怎样（过期、剪枝、删除、清空）都撤不回已投出去的那份——撤了会断前缀缓存。
"""
from __future__ import annotations

import uuid

# 便签寿命（API 调用条数）：40
NOTE_TTL_CALLS = 40
# 单条便签长度上限：便签是"顺手记一句"，不是文档
MAX_NOTE_CHARS = 300

KINDS = ("todo", "plan")


def make_note(
    text: str, from_context_ref: str = "", from_label: str = "",
    call_count: int = 0, kind: str = "todo",
) -> dict:
    """构建一条便签。expires_at_call 把「40 条」换算成绝对刻度，过期判定不用再算差值。"""
    text = (text or "").strip()[:MAX_NOTE_CHARS]
    kind = kind if kind in KINDS else "todo"
    return {
        "id": uuid.uuid4().hex[:8],
        "kind": kind,
        "text": text,
        "from_context_ref": str(from_context_ref or ""),
        "from_label": from_label or "",
        "created_call": int(call_count or 0),
        "expires_at_call": int(call_count or 0) + NOTE_TTL_CALLS,
    }


def is_expired(note: dict, call_count: int) -> bool:
    return int((note or {}).get("expires_at_call") or 0) <= int(call_count or 0)


def live_notes(notes: list[dict], call_count: int) -> list[dict]:
    """还能投递的便签（读取时的唯一过期入口）。"""
    return [n for n in (notes or []) if isinstance(n, dict) and not is_expired(n, call_count)]


def deliverable_notes(
    notes: list[dict], current_context_ref: str, call_count: int, delivered_ids: list[str],
) -> list[dict]:
    """这一轮该投给当前会话的便签（已投过的、自己写的、过期的都不算）。"""
    current = str(current_context_ref or "")
    already = set(delivered_ids or [])
    out: list[dict] = []
    for note in live_notes(notes, call_count):
        if note.get("id") in already:
            continue
        if current and str(note.get("from_context_ref") or "") == current:
            continue  # 自己写的不念给自己听
        out.append(note)
    return out


def note_copy(note: dict) -> dict:
    """投递时抄进会话帧的那份（只留渲染要用的字段）。"""
    return {
        "id": note.get("id"),
        "kind": note.get("kind") or "todo",
        "text": note.get("text", ""),
        "from_label": note.get("from_label") or "",
    }


def format_note_delivery(note: dict) -> str:
    """投递条目文案（唯一来源）：与 `format_frame_notes` 里那一行同款，但不带块头——

    投递改成逐条落账本条目了，条目自带位置（时间序列里的一次性事件），不需要块。
    """
    where = f"（来自 {note['from_label']}）" if note.get('from_label') else ""
    return f"[便签 · {note.get('kind') or 'todo'}] {note.get('text', '')}{where}"


def format_frame_notes(copies: list[dict]) -> str:
    """会话里那份便签 → 前缀块（每轮字节一致才缓存得住）。

    历史：主站已改成逐条投递条目（见 `format_note_delivery`），这个块渲染保留给世界侧/工具回显。

    这里**不看 `retired`**：前缀一旦投出去就归锁管，撤下也不许改它一个字节。
    "撤下"走尾部动态告知（`format_retired_notes_notice`），解锁（compact/clear）时才真删副本。
    """
    if not copies:
        return ""
    lines = [
        "## 📌 别的会话留给你的便签",
        "你在别的会话里顺手记下的临时事项：",
    ]
    for note in copies:
        where = f"（来自 {note['from_label']}）" if note.get("from_label") else ""
        lines.append(f"  [{note.get('kind') or 'todo'}] {note.get('text', '')}{where}")
    lines.append("（长期要记住的用 update_self_config 写进你自己的提示词；便签只用来带临时约定。）")
    return "\n".join(lines)


def format_retired_notes_notice(copies: list[dict]) -> str:
    """被撤下的便签 → 尾部变更通知（**发一次就完事**）。

    为什么放尾部：前缀必须字节稳定，撤下不能去改那几行。发一次而不是每轮发：
    它跟「能力变更通知」同款——告知一次，之后靠 AI 自己的行动延续；调用方发完就
    给副本打 `notified`，所以同一段对话只会看到一次。解锁时副本被删，痕迹一并消失。
    """
    dead = [c for c in (copies or []) if c.get("retired") and not c.get("notified")]
    if not dead:
        return ""
    lines = [
        "## 📌 便签撤下通知",
        "你已经把下面这几条便签撤下了——它们**作废，不要再照着执行**：",
    ]
    for note in dead:
        where = f"（来自 {note['from_label']}）" if note.get("from_label") else ""
        lines.append(f"- {note.get('text', '')}{where}")
    lines.append("（上面的便签块里还列着它们，是因为那段前缀必须保持字节稳定；以本通知为准。）")
    return "\n".join(lines)


def notes_brief(notes: list[dict], call_count: int) -> list[dict]:
    """便签 → 工具输出用的清单（带剩余调用数，AI 好判断还剩多久）。"""
    out = []
    for note in live_notes(notes, call_count):
        out.append({
            "id": note.get("id"),
            "kind": note.get("kind") or "todo",
            "text": note.get("text", ""),
            "from": note.get("from_label") or note.get("from_context_ref") or "",
            "remaining_calls": max(0, int(note.get("expires_at_call") or 0) - int(call_count or 0)),
        })
    return out
