"""
AI 状态栈服务 — push/pop/close/list 状态帧 + 提示词注入。

纯函数（utils/pure/state_stack.py）处理数据结构，本层负责 DB 编排。
"""
import json
import logging
from datetime import datetime, timezone
from sqlalchemy import text, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.agent import Agent
from app.repositories.agent_repo import AgentRepository, SQLAlchemyAgentRepository
from app.utils.pure.state_stack import (
    make_state_frame, format_state_stack_summary, format_handoff_tail, MAX_STACK_DEPTH,
)
from app.utils.pure.emotion import decay_emotion, apply_emotion_update

logger = logging.getLogger(__name__)


def _ensure_repo(db_or_repo):
    """兼容旧调用：传入 AsyncSession 时包装为 SQLAlchemyAgentRepository。"""
    if isinstance(db_or_repo, AsyncSession):
        return SQLAlchemyAgentRepository(db_or_repo)
    return db_or_repo


# ═══════════════════════════════════════════════════════════════
# DB 读写
# ═══════════════════════════════════════════════════════════════

async def _get_stack(db: AsyncSession, agent_id: int) -> list[dict]:
    """读取 agent 的状态栈（返回可修改的副本）。"""
    db = _ensure_repo(db)
    result = await db.execute(
        select(Agent.state_stack).where(Agent.id == agent_id)
    )
    row = result.scalar_one_or_none()
    if row is None or not isinstance(row, list):
        return []
    return list(row)


async def _set_stack(db: AsyncSession, agent_id: int, stack: list[dict]) -> None:
    """写入 agent 的状态栈。"""
    db = _ensure_repo(db)
    await db.execute(
        text("UPDATE agents SET state_stack = :stack WHERE id = :aid"),
        {"stack": json.dumps(stack, ensure_ascii=False), "aid": agent_id},
    )


async def load_trigger_state(db: AsyncSession, agent_id: int) -> dict:
    """读当前会话的触发规则状态（{tool_uses, delivered}）。

    挂在栈顶帧上：没有帧就没有会话，"本会话第几次"无从谈起，返回空状态。
    """
    db = _ensure_repo(db)
    stack = await _get_stack(db, agent_id)
    if not stack:
        return {"tool_uses": {}, "delivered": {}}
    top = stack[-1]
    return {
        "tool_uses": dict(top.get("tool_uses") or {}),
        "delivered": dict(top.get("delivered") or {}),
    }


async def save_trigger_state(db: AsyncSession, agent_id: int, state: dict) -> None:
    """写回当前会话的触发规则状态（没有帧就丢弃：状态属于会话，不属于 AI）。"""
    db = _ensure_repo(db)
    stack = await _get_stack(db, agent_id)
    if not stack:
        return
    stack[-1]["tool_uses"] = dict((state or {}).get("tool_uses") or {})
    stack[-1]["delivered"] = dict((state or {}).get("delivered") or {})
    await _set_stack(db, agent_id, stack)


async def set_active_semantic_focus(db: AsyncSession, agent_id: int, focus_id: str) -> str:
    """把栈顶帧的当前语义焦段换成 focus_id（空串 = 清空）。

    没有帧时什么都不做：语义焦段是「这段会话正在聊什么」，没有会话就无处可挂。
    """
    db = _ensure_repo(db)
    stack = await _get_stack(db, agent_id)
    if not stack:
        return ""
    stack[-1]["semantic_focus"] = str(focus_id or "")
    await _set_stack(db, agent_id, stack)
    return stack[-1]["semantic_focus"]


async def get_active_semantic_focus(db: AsyncSession, agent_id: int) -> str:
    """读栈顶帧的当前语义焦段。"""
    db = _ensure_repo(db)
    stack = await _get_stack(db, agent_id)
    return str(stack[-1].get("semantic_focus") or "") if stack else ""


def _left_conversation(prev: dict | None) -> dict:
    """离开一个会话/状态时打的交接包：从哪来、在干嘛、那段对话的原文尾巴。

    「原文尾巴」是临时性的：切回去时只注入一次，注入后即清（见 frame_turn_context）。
    统一入口——push_state（AI 主动切）与 ensure_active_frame（消息触发切）都从这里取，
    免得两处各写一份字段名。
    """
    if not prev:
        return {}
    return {
        "from_type": prev.get("type"),
        "from_context_ref": prev.get("context_ref") or "",
        "from_label": prev.get("label") or prev.get("context_ref") or "",
        "from_doing": (prev.get("doing") or prev.get("why") or "")[:200],
        "tail": prev.get("tail") or [],
    }


# ═══════════════════════════════════════════════════════════════
# 编排函数
# ═══════════════════════════════════════════════════════════════

async def push_state(
    db: AsyncSession, agent_id: int, frame: dict,
) -> tuple[list[dict], str]:
    """
    Push 新状态帧到栈顶。自动将原栈顶 active → paused。
    返回 (新栈, 消息)。
    """
    db = _ensure_repo(db)
    stack = await _get_stack(db, agent_id)

    if len(stack) >= MAX_STACK_DEPTH:
        return stack, f"状态栈已达上限 {MAX_STACK_DEPTH}，无法再 push"

    # 去重：栈顶与新帧 type + context_ref 相同 → 合并更新，不 push
    if stack and stack[-1].get("type") == frame.get("type") and stack[-1].get("context_ref") == frame.get("context_ref"):
        stack[-1].update({k: v for k, v in frame.items() if v is not None and k not in ("id", "created_at", "status")})
        await _set_stack(db, agent_id, stack)
        logger.info(f"Agent({agent_id}) push 去重: [{frame.get('type')}] {frame.get('context_ref', '')}")
        return stack, f"状态帧 [{frame.get('type')}] 已存在，已合并更新"

    # 原栈顶 active → paused，并作为新帧的“来源状态情感”+“交接信息”
    source_emotion = {}
    handoff = {}
    if stack and stack[-1].get("status") == "active":
        prev = stack[-1]
        prev["status"] = "paused"
        source_emotion = {
            "type": prev.get("type"),
            "emotion": prev.get("emotion") or {},
            "emotion_text": prev.get("emotion_text") or "",
        }
        # 交接打包：从哪来 / 在干嘛 / 原文尾巴（旧交接不重复注入——切换时一次性携带）
        handoff = _left_conversation(prev)

    frame["status"] = "active"
    if not frame.get("source_emotion"):
        frame["source_emotion"] = source_emotion
    if not frame.get("handoff"):
        frame["handoff"] = handoff
    stack.append(frame)

    await _set_stack(db, agent_id, stack)

    # P4: 自动写 JOURNAL
    await _auto_journal(db, agent_id, "push", frame)
    # P4: 自动追加 TODO
    await _auto_todo(db, agent_id, frame)

    logger.info(f"Agent({agent_id}) push [{frame.get('type')}]: {frame.get('doing', '')[:50]}")
    return stack, f"已压入状态帧 [{frame.get('type')}]"


async def pop_state(
    db: AsyncSession, agent_id: int, target_frame_id: str = "",
) -> tuple[list[dict], str]:
    """
    Pop 栈顶状态帧，选择性回跳：
    - target_frame_id 为空 → 回到上一层（LIFO）
    - target_frame_id 指定 → 直接回到目标帧，中间帧归档（completed，写 journal）
    恢复帧记录 completed_handoff（刚完成啥 + 跳过层），摘要注入“回来的交接”。
    返回 (新栈, 消息)。
    """
    db = _ensure_repo(db)
    stack = await _get_stack(db, agent_id)

    if not stack:
        return [], "状态栈为空，无需弹出"

    popped = stack.pop()
    skipped: list[str] = []

    if target_frame_id:
        # 选择性回跳：找到目标帧，中间的帧归档
        idx = next((i for i, f in enumerate(stack) if f.get("id") == target_frame_id), None)
        if idx is None:
            # 目标不存在 → 回退为 LIFO（不破坏栈）
            stack.append(popped)
            return stack, f"未找到目标状态帧 {target_frame_id}，已回退为回到上一层"
        skipped = [f"[{f.get('type')}]({(f.get('doing') or f.get('why') or '?')[:40]})"
                   for f in stack[idx + 1:]]
        for f in stack[idx + 1:]:
            f["status"] = "completed"
            await _auto_journal(db, agent_id, "pop", f)  # 归档写 journal
        stack = stack[:idx + 1]
        if stack[-1].get("status") == "paused":
            stack[-1]["status"] = "active"
    else:
        # LIFO：弹栈顶，恢复下一层
        if stack and stack[-1].get("status") == "paused":
            stack[-1]["status"] = "active"

    # 恢复帧记录“回来的交接”：刚完成啥 + 跳过了哪些层
    if stack:
        stack[-1]["completed_handoff"] = {
            "type": popped.get("type"),
            "doing": (popped.get("doing") or popped.get("why") or "")[:200],
            "skipped": " → ".join(skipped) if skipped else "",
            "label": popped.get("label") or popped.get("context_ref") or "",
            "tail": popped.get("tail") or [],
        }

    await _set_stack(db, agent_id, stack)

    # P4: 自动写 JOURNAL（弹栈帧）
    await _auto_journal(db, agent_id, "pop", popped)

    logger.info(f"Agent({agent_id}) pop [{popped.get('type')}]"
                + (f" 跳过 {len(skipped)} 帧" if skipped else ""))

    if stack:
        nf = stack[-1]
        suffix = f"（跳过 {len(skipped)} 帧）" if skipped else ""
        return stack, f"已弹出 [{popped.get('type')}]，恢复到 [{nf.get('type')}]: {nf.get('doing', nf.get('why', ''))}{suffix}"
    return stack, f"已弹出 [{popped.get('type')}]，状态栈已空"


async def _pop_and_resume(stack: list[dict], index: int = -1) -> dict:
    """弹出指定帧（默认栈顶），若新的栈顶是 paused 则恢复为 active。返回被弹的帧。"""
    frame = stack.pop(index)
    if stack and stack[-1].get("status") == "paused":
        stack[-1]["status"] = "active"
    return frame


async def close_state(
    db: AsyncSession, agent_id: int, frame_id: str = "",
) -> tuple[list[dict], str]:
    """
    关闭指定帧或栈顶帧（不恢复下层，除非下层是 paused）。
    frame_id 为空时关闭栈顶。
    返回 (新栈, 消息)。
    """
    db = _ensure_repo(db)
    stack = await _get_stack(db, agent_id)

    if not stack:
        return [], "状态栈为空，无需关闭"

    if frame_id:
        idx = next((i for i, f in enumerate(stack) if f.get("id") == frame_id), None)
        if idx is None:
            return stack, f"未找到状态帧 {frame_id}"
        frame = await _pop_and_resume(stack, idx)
    else:
        frame = await _pop_and_resume(stack)
    frame["status"] = "closed"

    await _set_stack(db, agent_id, stack)
    logger.info(f"Agent({agent_id}) close [{frame.get('type')}]({frame.get('id')})")
    return stack, f"已关闭状态帧 [{frame.get('type')}]"


async def list_states(db: AsyncSession, agent_id: int) -> list[dict]:
    """获取当前状态栈（工具用）。"""
    db = _ensure_repo(db)
    return await _get_stack(db, agent_id)


async def get_state_stack_summary(db: AsyncSession, agent_id: int, max_chars: int | None = None) -> str:
    """获取状态栈摘要文本（注入 prompt 用）。max_chars 默认 500（可被 agent 配置覆盖）。"""
    db = _ensure_repo(db)
    stack = await _get_stack(db, agent_id)
    if not stack:
        return ""
    if max_chars is None:
        from sqlalchemy import text as _text
        row = (await db.execute(_text("SELECT state_stack_max_chars FROM agents WHERE id = :aid"),
                                {"aid": agent_id})).first()
        max_chars = int(row[0]) if row and row[0] else 500
    # 焦段归属每轮复述给 AI：它不必重新判断自己在哪些焦段里，也看得见当初怎么归的
    from app.services.agent import focus_service
    from app.utils.pure import focus as pure_focus

    top = stack[-1]
    foci = await focus_service.load(db, agent_id)
    focus_line = pure_focus.describe(foci, top.get("context_ref") or "",
                                     top.get("semantic_focus") or "")
    return format_state_stack_summary(stack, max_chars=max_chars, focus_line=focus_line)


async def set_frame_notes(db: AsyncSession, agent_id: int, context_ref: str, copies: list[dict]) -> list[dict]:
    """把「已投递到本会话的便签」写进栈顶帧，返回落地后的那份。

    只认栈顶帧 = 本会话：切换没走完时栈顶还是别的会话，硬写会把 A 的便签记到 B 头上。
    """
    db = _ensure_repo(db)
    stack = await _get_stack(db, agent_id)
    if not stack or str(stack[-1].get("context_ref")) != str(context_ref):
        return []
    stack[-1]["notes"] = copies
    await _set_stack(db, agent_id, stack)
    return copies


async def retire_frame_notes(db: AsyncSession, agent_id: int, note_ids: set[str]) -> int:
    """把各会话帧里这些便签副本标成「已撤下」（AI 删记录 / 清空时调用）。

    只加标记、不删副本：删了会让那段前缀少一块（缓存断），而且 AI 也看不出自己撤过什么。
    帧里的副本是"已经过户给这段会话"的那份，改它属于会话自己的事，所以放在本模块。
    """
    db = _ensure_repo(db)
    if not note_ids:
        return 0
    stack = await _get_stack(db, agent_id)
    hit = 0
    for frame in stack:
        for copy in (frame.get("notes") or []):
            if copy.get("id") in note_ids and not copy.get("retired"):
                copy["retired"] = True
                hit += 1
    if hit:
        await _set_stack(db, agent_id, stack)
    return hit


async def mark_frame_notes_notified(db: AsyncSession, agent_id: int, note_ids: set[str]) -> int:
    """给撤销下的便签副本盖章「通知已发」——那条尾部通知只发一次（见 format_retired_notes_notice）。

    印章和通知在同一次构建里落库，所以断电/异常最多多发一次，不会漏发。
    """
    db = _ensure_repo(db)
    if not note_ids:
        return 0
    stack = await _get_stack(db, agent_id)
    hit = 0
    for frame in stack:
        for copy in (frame.get("notes") or []):
            if copy.get("id") in note_ids and not copy.get("notified"):
                copy["notified"] = True
                hit += 1
    if hit:
        await _set_stack(db, agent_id, stack)
    return hit


async def release_active_frame_notes(db: AsyncSession, agent_id: int) -> int:
    """解锁（compact / clear）时丢掉本会话帧里的便签副本——便签（连同撤下通知）就此离开这段上下文。

    前缀本来就要在解锁时重建，所以此刻删不额外付缓存代价；锁定态绝不能碰它。
    只认对话帧（dm / group_chat）：AI 手动压的状态帧不代表会话，别误清。
    """
    db = _ensure_repo(db)
    stack = await _get_stack(db, agent_id)
    if not stack or stack[-1].get("type") not in ("dm", "group_chat"):
        return 0
    copies = stack[-1].get("notes") or []
    if not copies:
        return 0
    stack[-1]["notes"] = []
    await _set_stack(db, agent_id, stack)
    logger.info(f"Agent({agent_id}) 解锁：丢掉会话帧上的 {len(copies)} 条便签副本")
    return len(copies)


async def frame_turn_context(
    db: AsyncSession, agent_id: int, context_ref: str, tail: list[str],
) -> str:
    """一轮开始时的状态帧记账（一次读 + 最多一次写），返回要注入提示词的一次性尾巴。

    1. 把本会话的最后几轮原文存进栈顶帧——它是「原文尾巴」的来源，切走时随交接带走；
    2. 取出并清空上一段对话留下的尾巴——临时性交接，只注入一轮，避免每轮重复喂。

    尾巴只在栈顶帧就是当前会话时才存：栈顶是别的会话（切换还没走完）时硬写会写错帧。
    """
    db = _ensure_repo(db)
    stack = await _get_stack(db, agent_id)
    if not stack:
        return ""

    top = stack[-1]
    dirty = False

    if tail and context_ref and str(top.get("context_ref")) == str(context_ref):
        if top.get("tail") != tail:
            top["tail"] = tail
            dirty = True

    # 一次性消费：handoff（切过来）优先，其次 completed_handoff（pop 回来）
    block = ""
    for key in ("handoff", "completed_handoff"):
        pending = top.get(key) or {}
        pending_tail = pending.get("tail") or []
        if not pending_tail:
            continue
        if not block:
            label = pending.get("from_label") or pending.get("label") or pending.get("from_context_ref") or ""
            reason = top.get("why") or ""
            block = format_handoff_tail(label, pending_tail, reason=reason)
        pending.pop("tail", None)
        top[key] = pending
        dirty = True

    if dirty:
        await _set_stack(db, agent_id, stack)
    return block


# ═══════════════════════════════════════════════════════════
# 会话帧自动维护（2026-08-09）：聊天即情景
# ═══════════════════════════════════════════════════════════

async def ensure_active_frame(
    db: AsyncSession, agent_id: int,
    conv_type: str, context_ref: str,
    title: str, actor_name: str,
) -> None:
    """
    确保状态栈栈顶帧 = 当前会话（DM/群聊触发时自动调用）。

    聊天是一个情景：AI 切换会话时自动记录「我去干什么 / 我干了什么 /
    回去接着干什么」，切回时带交接摘要，使 AI 在任何会话都能感知手头的事。

    - 栈顶已是本会话：不动（同一会话继续）
    - 栈里有本会话挂起帧：提到栈顶恢复 active，写 completed_handoff
      （📝 刚完成：刚才在别的会话干了什么）
    - 栈里没有：旧栈顶挂起（保留其 why/doing），push 新帧
    - 栈顶是 AI 手动帧（非 dm/group_chat）：不干预，等 AI 自己 pop

    注：超配额不触发回复时也会调用（帧记录「有人找过」），
    AI 切回其他会话时通过摘要感知未处理的消息。
    """
    db = _ensure_repo(db)
    if conv_type not in ("dm", "group_chat"):
        return
    stack = await _get_stack(db, agent_id)

    conv_label = "私信" if conv_type == "dm" else "群"

    # 栈顶已是当前会话
    if stack and stack[-1].get("context_ref") == context_ref:
        return
    # 栈顶是 AI 手动帧：不干预
    if stack and stack[-1].get("type") not in ("dm", "group_chat"):
        return

    idx = next((i for i, f in enumerate(stack) if f.get("context_ref") == context_ref), None)

    label = f"{conv_label}「{title}」"

    if idx is not None:
        # 切回挂起的会话：把「刚离开那段对话」的尾巴挂上，切回瞬间才知道刚才在别处说到哪
        frame = stack.pop(idx)
        prev = stack[-1] if stack else None
        if prev is not None:
            prev["status"] = "suspended"
            frame["completed_handoff"] = {
                "type": prev.get("type"),
                "doing": prev.get("doing"),
                "label": prev.get("label") or prev.get("context_ref") or "",
                "tail": prev.get("tail") or [],
            }
        frame["status"] = "active"
        frame["label"] = label
        frame["why"] = f"收到{actor_name}的消息"
        frame["doing"] = f"在{conv_label}「{title}」中回复{actor_name}"
        stack.append(frame)
    else:
        # 新会话：旧栈顶挂起 + push（handoff 写在新帧上——渲染读栈顶帧）
        prev = stack[-1] if stack else None
        frame = make_state_frame(
            type_=conv_type,
            context_ref=context_ref,
            label=label,
            why=f"收到{actor_name}的消息",
            doing=f"在{conv_label}「{title}」中回复{actor_name}",
        )
        if prev is not None:
            prev["status"] = "suspended"
            frame["handoff"] = _left_conversation(prev)
        stack.append(frame)

    if len(stack) > MAX_STACK_DEPTH:
        stack = stack[-MAX_STACK_DEPTH:]
    await _set_stack(db, agent_id, stack)


async def bump_frame_call_count(db: AsyncSession, agent_id: int, calls: int = 1) -> None:
    """LLM 每次调用后：agent 总计数 +1；栈顶 active 帧 call_count +1 并做情感衰减
    （mood homeostasis——情感随该状态自己的调用次数回归基线）。"""
    db = _ensure_repo(db)
    from sqlalchemy import text as _text
    # agent 总计数
    await db.execute(
        _text("UPDATE agents SET llm_call_count = llm_call_count + :c WHERE id = :aid"),
        {"c": calls, "aid": agent_id},
    )
    # 栈顶帧计数 + 情感衰减
    stack = await _get_stack(db, agent_id)
    if stack:
        top = stack[-1]
        if top.get("status") == "active":
            top["call_count"] = int(top.get("call_count") or 0) + calls
            if top.get("emotion"):
                top["emotion"] = decay_emotion(top["emotion"], calls)
            await _set_stack(db, agent_id, stack)


async def update_active_emotion(db: AsyncSession, agent_id: int, update) -> dict:
    """更新栈顶帧情感（情感工具用）：增量（"+0.2"）/ 完整向量 / 概括词。
    无 active 帧时写 agent 级情感暂存（context_ref="" 的隐式帧不存在则忽略）。"""
    db = _ensure_repo(db)
    stack = await _get_stack(db, agent_id)
    if not stack:
        return {}
    top = stack[-1]
    top["emotion"] = apply_emotion_update(top.get("emotion") or {}, update)
    top["emotion_text"] = ""  # 向量化后清文字（摘要优先显示向量）
    await _set_stack(db, agent_id, stack)
    return top["emotion"]


async def set_active_emotion_text(db: AsyncSession, agent_id: int, text: str) -> None:
    """设置栈顶帧文字心情（未向量化模式）。"""
    db = _ensure_repo(db)
    stack = await _get_stack(db, agent_id)
    if not stack:
        return
    stack[-1]["emotion_text"] = text[:100]
    await _set_stack(db, agent_id, stack)


async def get_active_emotion(db: AsyncSession, agent_id: int) -> dict:
    """读栈顶帧情感（向量 + 文字），供注入/展示。"""
    db = _ensure_repo(db)
    stack = await _get_stack(db, agent_id)
    if not stack:
        return {}
    top = stack[-1]
    return {
        "emotion": top.get("emotion") or {},
        "emotion_text": top.get("emotion_text") or "",
        "source_emotion": top.get("source_emotion") or {},
        "call_count": top.get("call_count") or 0,
    }


async def get_active_frame_tools(db: AsyncSession, agent_id: int) -> tuple[list[str] | None, list[str] | None]:
    """读栈顶帧的工具/技能白名单（None = 不隔离，保持全局）。"""
    db = _ensure_repo(db)
    stack = await _get_stack(db, agent_id)
    if not stack:
        return None, None
    top = stack[-1]
    return top.get("tools"), top.get("skills")


async def persist_last_task_as_state(
    db: AsyncSession, agent_id: int, last_task: str,
    group_id: int | None, context_ref: str = "",
) -> None:
    """
    end_turn 兜底：状态栈为空但有 last_task 时，自动 push 一个帧。
    防止 AI 在做的事在下次激活时丢失。
    """
    db = _ensure_repo(db)
    stack = await _get_stack(db, agent_id)

    if not stack and last_task:
        frame = make_state_frame(
            type_="group_chat" if group_id else "dm",
            context_ref=context_ref or (f"group:{group_id}" if group_id else ""),
            why=last_task[:200],
            doing=last_task[:200],
        )
        stack.append(frame)
        await _set_stack(db, agent_id, stack)
        logger.info(f"Agent({agent_id}) 自动 push（end_turn 兜底）: {last_task[:50]}")


# ═══════════════════════════════════════════════════════════════
# P4: workspace 自动联动
# ═══════════════════════════════════════════════════════════════

async def _auto_journal(db: AsyncSession, agent_id: int, action: str, frame: dict) -> None:
    """push/pop 时自动写 JOURNAL。非致命——失败静默忽略。"""
    db = _ensure_repo(db)
    try:
        from app.services.agent.workspace_service import get_workspace_file, set_workspace_file
    except ImportError:
        return

    try:
        existing = await get_workspace_file(db, agent_id, "journal") or ""
        ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
        type_ = frame.get("type", "?")
        doing = frame.get("doing", "")
        why = frame.get("why", "")

        if action == "push":
            entry = f"## {ts}\n切换 [{type_}]: {why or doing}\n"
            if frame.get("todo"):
                entry += f"- TODO: {frame['todo']}\n"
        else:
            entry = f"## {ts}\nEND [{type_}]: {doing} | 状态: 完成\n"

        sep = "\n---\n" if existing else ""
        await set_workspace_file(db, agent_id, "journal", entry + sep + existing)
    except Exception:
        pass


async def _auto_todo(db: AsyncSession, agent_id: int, frame: dict) -> None:
    """push 时自动追加 TODO 项。非致命——失败静默忽略。"""
    db = _ensure_repo(db)
    if not frame.get("todo"):
        return
    try:
        from app.services.agent.workspace_service import get_workspace_file, set_workspace_file
    except ImportError:
        return

    try:
        existing = await get_workspace_file(db, agent_id, "todo") or ""
        type_ = frame.get("type", "?")
        todo = frame["todo"].strip().replace("\n", "; ")
        new_line = f"- [ ] [{type_}] {todo}\n"
        await set_workspace_file(db, agent_id, "todo", new_line + existing)
    except Exception:
        pass
