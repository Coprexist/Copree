"""
状态栈纯函数 — 无 IO、无 DB 依赖。

make_state_frame(): 构建单个状态帧（交接驱动：handoff/completed_handoff）
format_state_stack_summary(): 栈 → AI 可读摘要（只渲染当前帧 + 交接信息）

情感向量纯函数见 emotion.py（独立模块）。
"""
from __future__ import annotations

from datetime import datetime, timezone
import uuid

from app.utils.pure.emotion import (
    normalize_emotion, emotion_to_text,
)


MAX_STACK_DEPTH = 10

# 帧的合法扩展字段（make_state_frame 白名单）
_FRAME_FIELDS = (
    "id", "type", "context_ref", "label", "why", "doing", "todo", "plan", "journal",
    "created_at", "status", "emotion", "emotion_text", "source_emotion",
    "tools", "skills", "call_count", "handoff", "completed_handoff",
    # tail：这段会话的最后几轮原文。切走时它是「原文尾巴」，切回来时一次性注入
    "tail",
    # notes：投递进这段会话的跨状态便签副本（固化在前缀里，直到 compact/clear）
    "notes",
)


def make_state_frame(type_: str, context_ref: str = "", **extras) -> dict:
    """构建单个状态帧（纯函数）。extras 只收白名单字段，其余静默忽略。

    常用 extras：why（为什么切换）/ doing（在干嘛）/ todo（回去继续啥）/
    plan / emotion（情感向量）/ emotion_text（文字心情）/ tools、skills（工具隔离）/
    handoff、completed_handoff（交接信息）。
    """
    frame = {
        "id": uuid.uuid4().hex[:12],
        "type": type_,
        "context_ref": context_ref,
        "why": "",
        "doing": "",
        "todo": "",
        "plan": "",
        "journal": "",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "active",
        "emotion": {},
        "emotion_text": "",
        "source_emotion": {},
        "tools": None,
        "skills": None,
        "call_count": 0,
        "handoff": {},
        "completed_handoff": {},
    }
    for key, value in extras.items():
        if key in _FRAME_FIELDS and value is not None:
            frame[key] = value
    frame["emotion"] = normalize_emotion(frame["emotion"])
    return frame


def frame_tail(messages: list[dict], max_exchanges: int = 4, max_chars: int = 1200) -> list[str]:
    """取「最后几轮对话原文」（切走时靠它保持连续，无需额外 LLM 调用）。

    只认 user/assistant 且有文本内容的消息：system 段（规矩、时间、摘要）不是
    对话原文，混进来只会让 AI 把注入内容当成对方说过的话。按「轮」收集——收集满
    max_exchanges 条用户消息（连同其后的回复）即停；再从最旧端按 max_chars 丢，
    因为尾巴的意义就是「最新说到哪」。
    """
    picked: list[str] = []
    users = 0
    for m in reversed(messages):
        if m.get("role") not in ("user", "assistant"):
            continue
        content = m.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        if m.get("role") == "user":
            users += 1
        picked.append(content.strip())
        if users >= max_exchanges:
            break
    picked.reverse()

    # 超预算从最旧端丢；最后一条即使自己就超预算也留着——丢掉它等于这次交接白做
    # （实测：AI 上一轮的长回复单条就上千字，整条丢会让尾巴变成空）。
    total = sum(len(x) + 1 for x in picked)
    while len(picked) > 1 and total > max_chars:
        total -= len(picked[0]) + 1
        picked.pop(0)
    if picked and len(picked[0]) > max_chars:
        picked[0] = picked[0][:max_chars].rstrip() + "……（原文过长，已截断）"
    return picked


def format_handoff_tail(label: str, tail: list[str], reason: str = "") -> str:
    """渲染「上一段对话的原文尾巴」——临时性交接，只注入一次。

   两道边界都要说清（2026-09-25 实测踩过第一条的坑）：
    - **别串台**：群里看到私信的尾巴，AI 很容易顺手在群里答一句私信的内容；
    - **但约定要履约**：如果那段对话里说好了「到了别处做什么」（暗号、触发条件、待办），
      必须照做——只写禁令会让 AI 明明看见了暗号也不敢答（用户实测：私信约好暗号，
      群里喊了暗号，AI 只看见"便签：就剩暗号那条待验证"，却没看见答什么，也没敢接）。
    """
    if not tail:
        return ""
    where = f"（{label}）" if label else ""
    lines = [
        f"## 📎 上一段对话的原文尾巴{where}",
        "这是你刚离开的那段对话的最后原文：",
        "- 不要在当前会话里回应它、不要把它当成当前会话的消息，也不要向当前的人复述它；",
        "- 但如果那段对话里和你约定了**到了别处要做的事**（暗号、触发条件、待办），"
        "现在条件满足就按约定执行——这条提醒不构成不做它的理由。",
    ]
    if reason:
        lines.append(f"（你离开那里的原因：{reason}）")
    lines += [f"  {t}" for t in tail]
    lines.append(
        "（要是那段对话里还有该带到别处、但原文这里没说完的临时约定，用 cross_state_note 记下来："
        "40 次 API 调用内有效，别的会话拿到后会一直留在它的上下文里。）"
    )
    return "\n".join(lines)


def format_state_stack_summary(stack: list[dict], max_chars: int = 500) -> str:
    """栈 → AI 可读摘要（交接驱动）。

    只渲染「当前帧 + 交接信息」，不逐层展开历史帧：
    - 旧交接已在 LLM 对话历史里出现过（工具调用参数），不重复注入
    - 当前帧：doing / TODO / PLAN / 🎭 情感（完整）
    - handoff：本次切换的交接（← 从[来源]来，为什么，回去继续）
    - completed_handoff：pop 回来后刚完成的交接（📝 刚完成）
    - 嵌套提示：栈深 > 1 时给"共 N 帧"计数

    长度控制（max_chars 默认 500）：超限按降级阶梯（_RENDER_*），
    最新帧的 TODO/PLAN 永不丢。
    """
    if not stack:
        return ""
    top = stack[-1]

    def render_top() -> list[str]:
        lines = ["\n\n## 📋 当前状态"]
        status = top.get("status", "active")
        type_name = top.get("type", "?")
        context = top.get("label") or top.get("context_ref", "")
        doing = top.get("doing", "")
        why = top.get("why", "")
        todo = top.get("todo", "")
        plan = top.get("plan", "")

        marker = "▸▶" if status == "active" else "▸"
        context_str = f"({context})" if context else ""
        lines.append(f"{marker} [{type_name}] {context_str}: {doing or why}")
        if todo:
            lines.append(f"   TODO: {todo.strip().replace(chr(10), '; ')}")
        if plan:
            lines.append(f"   PLAN: {plan.strip().replace(chr(10), '; ')}")
        # 🎭 情感（含来源情感并置）
        emotion_text = top.get("emotion_text") or ""
        emotion_vec = top.get("emotion") or {}
        src_vec = (top.get("source_emotion") or {}).get("emotion") or {}
        if emotion_text:
            lines.append(f"   🎭 心情: {emotion_text}")
        elif any(v >= 0.05 for v in emotion_vec.values()):
            lines.append(f"   🎭 情感: {emotion_to_text(emotion_vec)}")
        if src_vec and any(v >= 0.05 for v in src_vec.values()):
            src_type = (top.get("source_emotion") or {}).get("type") or ""
            prefix = f"   ← 来源状态({src_type})情感: " if src_type else "   ← 来源状态情感: "
            lines.append(prefix + emotion_to_text(src_vec))
        if len(stack) > 1:
            lines.append(f"   ⏸ 另有 {len(stack) - 1} 帧未完成（可 list_states 查看）")
        return lines

    def render_handoff() -> list[str]:
        lines = []
        comp = top.get("completed_handoff") or {}
        if comp.get("type") or comp.get("doing"):
            lines.append(f"📝 刚完成: [{comp.get('type', '?')}] {comp.get('doing', '')}")
            if comp.get("skipped"):
                lines.append(f"   （跳过了 {comp['skipped']}）")
        handoff = top.get("handoff") or {}
        if handoff.get("from_type") or top.get("why"):
            src = f"[{handoff.get('from_type')}] {handoff.get('from_doing', '')}".strip() if handoff.get("from_type") else ""
            parts = [f"← 从{src}来" if src else "← 新状态"]
            if top.get("why"):
                parts.append(f"原因: {top['why']}")
            if todo := top.get("todo"):
                parts.append(f"回去继续: {todo.strip().replace(chr(10), '; ')}")
            lines.append("   " + " · ".join(parts))
        return lines

    def finish(lines: list[str]) -> str:
        lines.append("\n请继续执行当前任务。完成后调用 pop_state 回到上一层（可指定目标帧），或 close_state 放弃。")
        return "\n".join(lines)

    full = finish(render_top() + render_handoff())
    if len(full) <= max_chars:
        return full
    # 超限：砍交接的"原因"细节（保留结构主干）
    compact = finish(render_top() + [l for l in render_handoff() if not l.strip().startswith("原因")])
    if len(compact) <= max_chars:
        return compact
    return compact[:max_chars].rstrip() + "\n……（摘要过长，已截断）"
