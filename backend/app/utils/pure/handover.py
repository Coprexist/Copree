"""状态帧后事交接 —— 待交接清单与平台代销告知的文案（无 IO）

两条一次性事实，都**落历史**（进前缀：写一次、之后每轮命中缓存；放在尾部动态块里则是每轮全价）：
- **待交接**：这帧已经不在运行集合里，它留下的后事（记忆的作用域/锚点、要做的事）等他处理，
  处理完调 finish_frame 销掉；
- **平台代销**：平台绕过他表态把帧删了（积压超硬底 / 这档不接手后事 / 会话已消失三种来由），
  删得必须留痕，且要说清是哪种来由——他才知道这帧还要不要自己管。

幂等依据与便签同款：条目的 ref 里写身份（handover:{帧 id} / drop:{时刻}），账本里有了就不再投。
解锁（compact / clear）时随一次性条目离场——帧还在，下一段上下文会重新收到提醒。
"""
from __future__ import annotations

from datetime import datetime, timezone

HANDOVER_PREFIX = "handover:"
DROP_PREFIX = "drop:"

# 后事怎么办：写给 AI 的出路（工具都是现成的，别让它干瞪眼）
_HOWTO = (
    "记忆：锚在这个状态上的该改锚点（switch_focus / merge_focus），"
    "该重新记的用 store_memory 指对作用域；"
    "该帧的记录留到你说办完为止。"
)


def handover_ref(frame_id: str) -> str:
    """待交接告知的幂等键：一帧一条。"""
    return f"{HANDOVER_PREFIX}{frame_id}"


def drop_ref(at: str) -> str:
    """平台代销告知的幂等键：一次代销一条。"""
    return f"{DROP_PREFIX}{at}"


def delivered_refs(entries: list[dict]) -> set[str]:
    """账本里已经投过的后事相关 ref（投递幂等的唯一依据：AI 看过就算投过）。"""
    out: set[str] = set()
    for e in entries or []:
        ref = str((e or {}).get("ref") or "")
        if ref.startswith(HANDOVER_PREFIX) or ref.startswith(DROP_PREFIX):
            out.add(ref)
    return out


def _label(frame: dict) -> str:
    return str(frame.get("label") or frame.get("context_ref") or frame.get("type") or "?").strip()


def format_handover_notice(frame: dict) -> str:
    """一帧的待交接告知。"""
    fid = str(frame.get("id") or "")
    doing = str(frame.get("doing") or frame.get("why") or "").strip()
    lines = [f"【状态后事 · {_label(frame)}】",
             "这帧已经不在运行了，它留下的事还没处置：" + _HOWTO]
    if doing:
        lines.append(f"（它当时在：{doing[:60]}）")
    lines.append(f"办完调 finish_frame(frame_id=\"{fid}\") 销掉这帧。")
    return "\n".join(lines)


def drop_note(frames: list[dict], reason: str) -> dict:
    """一条「平台代销」事实（记在帧的 pending_notices 上，构建提示词那一步落成历史条目）。

    与 format_drop_notice 是同一件事的写侧与读侧：字段名只在这里出现一次，
    加一种来由时不会出现"文案认得、写侧没写"的半吊子。reason 认不出时按积压处理。
    """
    return {
        "kind": "platform_drop",
        "reason": reason if reason in _DROP_REASONS else "backlog",
        "at": datetime.now(timezone.utc).isoformat(),
        "frames": [{"id": f.get("id"), "type": f.get("type"),
                    "label": f.get("label") or f.get("context_ref") or ""} for f in frames],
    }


# 代销的三种来由：同一件事（平台替他删帧）的三个触发口，文案要说清是哪一种，
# 否则他分不清"我没交接完"和"这档本来就不归我交接"。
_DROP_REASONS = {
    "backlog": ("【平台代销 · 待交接帧积压超限】",
                "挂起的帧超过上限，平台代销了最旧的 {n} 帧（记录已删，不是你交接的）：",
                "以后挂起的帧请及时用 finish_frame 收尾，别让它们堆着。"),
    "profile": ("【平台代销 · 你这档不自己交接帧后事】",
                "帧位已满，平台替你收掉了最久没用的 {n} 帧（记录已删）：",
                "想自己处置帧后事（整理锚点与记忆、办完调 finish_frame 销掉），"
                "用 update_self_config 打开 retire_handover_self。"),
    "session": ("【平台代销 · 会话已不存在】",
                "你在这个会话里的 {n} 帧已随会话一起收掉（记录已删）：",
                "你在里面的共享记忆已改成你的私有记忆、锚点改到「所有聊天」，没有丢。"),
}


def format_drop_notice(note: dict) -> str:
    """一次平台代销的告知（代销是平台替他删的，必须说清来由与是哪几帧）。"""
    frames = list((note or {}).get("frames") or [])
    title, what, howto = _DROP_REASONS.get(
        str((note or {}).get("reason") or "backlog"), _DROP_REASONS["backlog"])
    lines = [title, what.format(n=len(frames))]
    for f in frames:
        lines.append(f"- [{f.get('type') or '?'}] {f.get('label') or '?'}（{f.get('id') or '?'}）")
    lines.append(howto)
    return "\n".join(lines)
