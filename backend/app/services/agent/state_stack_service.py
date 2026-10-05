"""
状态帧服务 — push/pop/close/list 状态帧 + 提示词注入。

两层要分清（这是全篇的前提）：
- **存储** = agents.state_stack 整个数组，全量保留，只在「他交接完后事」（finish_frame）时删；
- **运行集合** = 数组里 status 处于 active/paused/suspended 的那串指针，"栈"只剩它。

顺序不变量（normalize_order）：数组 = [历史区][运行区，当前帧在末尾]。读写各收口一次
（_get_stack 读时归一化、_save 唯一写入点），于是全仓 stack[-1] 恒等于当前帧。
身份不变量：一个 context_ref（一段会话）至多一帧——push 认出同会话就切回原帧（帧 id 稳定），
_save 再兜底归并（规则表见 pure.merge_same_context）；契约 docs/dev/frame_lifecycle.md。

纯函数在 utils/pure/state_stack.py，本层只做 DB 编排。
"""
import json
import logging
from datetime import datetime, timezone
from sqlalchemy import text, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.agent import Agent
from app.repositories.agent_repo import AgentRepository, SQLAlchemyAgentRepository
from app.utils.pure.handover import drop_note
from app.utils.pure.state_stack import (
    make_state_frame, format_state_stack_summary, format_handoff_tail,
    DEFAULT_FRAME_CAPACITY, ENDED_STATUS,
    by_id, context_frames, current, drop_context_frames, drop_overflow,
    drop_retired_overflow, is_running, merge_same_context, normalize_order, resume_target, retired,
    retire_context_frames, retire_overflow, running, touch,
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
    return normalize_order(list(row))


async def _write_stack(db: AsyncSession, agent_id: int, stack: list[dict]) -> None:
    """裸写入（不做顺序归一化与容量处置）——只有 _save 该调它。"""
    db = _ensure_repo(db)
    await db.execute(
        text("UPDATE agents SET state_stack = :stack WHERE id = :aid"),
        {"stack": json.dumps(stack, ensure_ascii=False), "aid": agent_id},
    )


async def _frame_capacity(db: AsyncSession, agent_id: int) -> int:
    """帧容量（AI 可自配）：NULL/0 回落到默认值。"""
    db = _ensure_repo(db)
    result = await db.execute(select(Agent.frame_capacity).where(Agent.id == agent_id))
    row = result.first()
    value = int(row[0]) if row and row[0] else 0
    return value or DEFAULT_FRAME_CAPACITY


async def _retire_handover_self(db: AsyncSession, agent_id: int) -> bool:
    """这档 AI 自己接手帧后事吗（预设档位定的，AI 可自改）。

    开 = 超容量的帧挂起待交接：记录留着，下一段上下文收到清单，他处置完调 finish_frame 销掉；
    关 = 平台直接代销：既然没人来交接，留着记录只是占地方，删掉并留一条告知。
    两种走法的分岔只在"留不留记录"，挑选规则同一套（pure 里的 _overflow）。
    """
    db = _ensure_repo(db)
    result = await db.execute(select(Agent.retire_handover_self).where(Agent.id == agent_id))
    row = result.first()
    return bool(row[0]) if row else False


def _attach_notices(stack: list[dict], agent_id: int, notes: list[dict]) -> None:
    """把待告知事实记在**当前帧**上，由构建提示词那一步落成历史条目。

    为什么记当前帧：一次性事实只有那一个落历史的地方（与便签/能力变更通知同一出口），
    而告知总要在一个会话里说给他听。当前帧就是"下一次开口"的那个会话。
    """
    if not notes:
        return
    cur = current(stack)
    if cur is None:
        logger.warning(f"Agent({agent_id}) 有 {len(notes)} 条帧代销告知，但当前没有会话帧可挂")
        return
    cur["pending_notices"] = list(cur.get("pending_notices") or []) + notes


async def _save(db: AsyncSession, agent_id: int, stack: list[dict]) -> list[dict]:
    """**帧的唯一写入点**：归一化顺序 + 同会话归并 + 容量处置，再落库。返回挂起待交接的帧 id。

    同一段会话归并（见 pure.merge_same_context）与容量处置都放这里：都是"帧本身的状态"，
    与"谁触发的那次写入"无关；分散到各调用点就会出现"push 一套、切会话另一套"。
    归一是**原地**的，调用方手里那份栈与落库的是同一份。
    走的方式按这档 AI 的取向分两条（挂起待交接 / 平台代销），但**都不静默**：
    挂起的会收到清单，代销的会收到一条「平台代销」告知。
    """
    db = _ensure_repo(db)
    # 原地归一 + 同会话归并：都是"帧本身的状态"，各 push 点不必各自记得
    stack[:] = normalize_order(stack)
    merged = merge_same_context(stack)
    if merged:
        logger.warning(f"Agent({agent_id}) 同一会话的重复帧已归并：{[f.get('id') for f in merged]}")
        stack[:] = normalize_order(stack)
    capacity = await _frame_capacity(db, agent_id)
    if await _retire_handover_self(db, agent_id):
        retired_ids = retire_overflow(stack, capacity)
        gone: list[dict] = []
    else:
        retired_ids = []
        gone = drop_overflow(stack, capacity)
    # 硬底与取向无关：挂起积压超限，谁都得被代销（他若不接手，上面那条路压根不会挂起）
    backlog = drop_retired_overflow(stack, capacity)
    notes = []
    if gone:
        notes.append(drop_note(gone, "profile"))
        logger.warning(f"Agent({agent_id}) 帧位已满且不接手后事：平台代销 {[f.get('id') for f in gone]}")
    if backlog:
        notes.append(drop_note(backlog, "backlog"))
        logger.warning(f"Agent({agent_id}) 待交接积压超限：平台代销 {[f.get('id') for f in backlog]}")
    _attach_notices(stack, agent_id, notes)
    await _write_stack(db, agent_id, stack)
    if retired_ids:
        logger.info(f"Agent({agent_id}) 帧容量超限：挂起待交接 {retired_ids}")
    return retired_ids


async def dispose_context_frames(db: AsyncSession, context_ref: str) -> dict:
    """会话从世界上消失了（群解散等）：把它名下的帧按这档 AI 的取向处置。

    与容量闸的区别是它不看容量、不等表态：会话已经不存在，帧留着只会让摘要把死会话当成
    "正在进行"。接手后事的挂起待交接（记下来等他一帧一帧办），不接手的由平台代销
    ——两条路都留告知，返回 {"retired": n, "dropped": n}。
    """
    db = _ensure_repo(db)
    rows = (await db.execute(
        select(Agent.id, Agent.state_stack, Agent.retire_handover_self))).all()
    out = {"retired": 0, "dropped": 0}
    for agent_id, raw, handover_self in rows:
        stack = normalize_order(list(raw or []))
        if not context_frames(stack, context_ref):
            continue
        if handover_self:
            ids = retire_context_frames(stack, context_ref)
            out["retired"] += len(ids)
            logger.info(f"Agent({agent_id}) 会话 {context_ref} 已消失：挂起待交接 {ids}")
        else:
            dropped = drop_context_frames(stack, context_ref)
            _attach_notices(stack, agent_id, [drop_note(dropped, "session")])
            out["dropped"] += len(dropped)
            logger.info(f"Agent({agent_id}) 会话 {context_ref} 已消失：平台代销 {[f.get('id') for f in dropped]}")
        await _write_stack(db, agent_id, stack)
    return out


async def get_frames(db: AsyncSession, agent_id: int) -> list[dict]:
    """全量存储的帧（历史区在前、当前帧在末尾）；含已结束与待交接的。"""
    return await _get_stack(db, agent_id)


async def restore_frame(db: AsyncSession, agent_id: int, frame_id: str,
                        origin_context_ref: str = "", updates: dict | None = None) -> dict:
    """把当前帧换成指定帧（闹钟唤醒用）：帧还在存储里就复活它，从没记过才重建同型帧。

    帧不再被 pop/close 删掉，所以"计划排给谁"这个指针长期有效——连已结束的帧都能按 id 复活。
    重建只发生在**这个 id 从来没存在过**时（老数据、或帧被代销/交接删了之后）；
    重建**沿用原 frame_id**，身份不因中间发生什么而换人。
    updates：复活时一并写回的字段——帧的身份没变、内容要跟着这次进来的地方更新
    （同世界换群进：世界帧上的通道群要指向这个群）。
    返回恢复好的帧（没有帧身份可用时返回 {}）。
    """
    db = _ensure_repo(db)
    stack = await _get_stack(db, agent_id)
    # 先认「当前帧」，再动栈：新帧一 append 就自己也成了 active，那时再取 current 会取到它自己
    prev = current(stack)
    # 在**全量存储**里按 id 找：帧不再被随手删，所以闹钟记的帧指针长期有效（连已结束的也能复活）
    frame = by_id(stack, frame_id)
    if frame is None:
        if not origin_context_ref:
            return {}
        frame = make_state_frame(
            # 键的口径有三种：群 group:{id}、世界 world:{id}、私信就是 session_id（context_sync.context_ref）
            type_=("group_chat" if origin_context_ref.startswith("group:")
                   else "world" if origin_context_ref.startswith("world:")
                   else "dm"),
            context_ref=origin_context_ref, id=frame_id or None,
            why="闹钟唤醒", doing="执行自己排下的计划",
        )
        stack.append(frame)
    if prev is not None and prev is not frame:
        prev["status"] = "suspended"
    if updates:
        frame.update(updates)
    frame["status"] = "active"
    touch(frame)
    await _save(db, agent_id, stack)
    return frame


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
    await _save(db, agent_id, stack)


async def reset_frame_trigger_state(db: AsyncSession, agent_id: int) -> int:
    """解锁（compact / clear）时清空本会话帧的触发规则状态：投递进度与调用计数随上下文重来。

    为什么：解锁就是另一段上下文，scope=frame 的"本帧投一次"该从零开始；
    只认对话帧（dm / group_chat），与 release_active_frame_notes 同口径。
    """
    db = _ensure_repo(db)
    stack = await _get_stack(db, agent_id)
    if not stack or stack[-1].get("type") not in ("dm", "group_chat"):
        return 0
    top = stack[-1]
    cleared = len(top.get("tool_uses") or {}) + len(top.get("delivered") or {})
    if not cleared:
        return 0
    top["tool_uses"] = {}
    top["delivered"] = {}
    await _save(db, agent_id, stack)
    logger.info(f"Agent({agent_id}) 解锁：复位触发规则状态（{cleared} 项）")
    return cleared


async def set_active_semantic_focus(db: AsyncSession, agent_id: int, focus_id: str) -> str:
    """把栈顶帧的当前语义焦段换成 focus_id（空串 = 清空）。

    没有帧时什么都不做：语义焦段是「这段会话正在聊什么」，没有会话就无处可挂。
    """
    db = _ensure_repo(db)
    stack = await _get_stack(db, agent_id)
    if not stack:
        return ""
    stack[-1]["semantic_focus"] = str(focus_id or "")
    await _save(db, agent_id, stack)
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
    """把状态切到这一帧：同会话（context_ref）已有活帧就**切回它**（就地更新，帧 id 不变），
    没有才压新帧。帧 id 是闹钟之类记着的长期指针，同会话换一次入口不能换 id。

    原当前帧压成 paused，「从哪来 / 在干嘛 / 原文尾巴」打成交接包记在切过去的那帧上。
    返回 (新栈, 消息)。
    """
    db = _ensure_repo(db)
    stack = await _get_stack(db, agent_id)
    # 先认当前帧再动栈：新帧一 append 就自己也成了 active，那时再取 current 会取到它自己
    prev = current(stack)

    same = context_frames(stack, str(frame.get("context_ref") or ""))
    existing = same[-1] if same else None
    if existing is None:
        target = frame
        stack.append(target)
    else:
        # 切回同一段会话：就地更新。push 只带"这次要写的"，没写的字段照旧（置空不动它）
        target = existing
        target.update({k: v for k, v in frame.items()
                       if v is not None and k not in ("id", "created_at", "status")})

    if prev is not None and prev is not target:
        prev["status"] = "paused"
        if not target.get("source_emotion"):
            target["source_emotion"] = {
                "type": prev.get("type"),
                "emotion": prev.get("emotion") or {},
                "emotion_text": prev.get("emotion_text") or "",
            }
        # 交接打包：从哪来 / 在干嘛 / 原文尾巴（旧交接不重复注入——切换时一次性携带）
        if not target.get("handoff"):
            target["handoff"] = _left_conversation(prev)
    target["status"] = "active"
    touch(target)

    await _save(db, agent_id, stack)

    if existing is None:
        # P4: 自动写 JOURNAL
        await _auto_journal(db, agent_id, "push", target)
        # P4: 自动追加 TODO
        await _auto_todo(db, agent_id, target)
        logger.info(f"Agent({agent_id}) push [{target.get('type')}]: {target.get('doing', '')[:50]}")
        return stack, f"已压入状态帧 [{target.get('type')}]"

    logger.info(f"Agent({agent_id}) 切回 [{target.get('type')}] {target.get('context_ref', '')}")
    return stack, f"已切回状态帧 [{target.get('type')}]（同一段会话只有一帧，帧身份不变）"


async def pop_state(
    db: AsyncSession, agent_id: int, target_frame_id: str = "",
) -> tuple[list[dict], str]:
    """结束当前帧并可选回跳：帧**只出运行集合（status=ended），记录保留在存储里**。

    - target_frame_id 为空 → 回到上一层（LIFO）
    - target_frame_id 指定 → 直接回到目标帧，中间仍在运行的帧一并结束（写 journal）
    恢复的帧记 completed_handoff（刚完成啥 + 跳过层），摘要注入"回来的交接"。
    返回 (全量存储, 消息)。
    """
    db = _ensure_repo(db)
    stack = await _get_stack(db, agent_id)

    live = running(stack)
    if not live:
        return [], "状态栈为空，无需弹出"

    popped = live[-1]                     # LIFO：弹出运行集合的末位
    popped["status"] = ENDED_STATUS       # 出运行集合；记录留在存储里，等他以后交接
    skipped: list[str] = []

    if target_frame_id:
        # 选择性回跳：找到目标帧，中间仍在运行的帧一并结束
        target = by_id(stack, target_frame_id)
        if target is None or not is_running(target):
            # 目标不存在（或已结束）→ 回退为 LIFO（不破坏栈）
            popped["status"] = "active"
            return stack, f"未找到目标状态帧 {target_frame_id}，已回退为回到上一层"
        rest = running(stack)
        cut = rest.index(target)
        skipped = [f"[{f.get('type')}]({(f.get('doing') or f.get('why') or '?')[:40]})"
                   for f in rest[cut + 1:]]
        for f in rest[cut + 1:]:
            f["status"] = ENDED_STATUS
            await _auto_journal(db, agent_id, "pop", f)  # 归档写 journal

    # 恢复帧记录“回来的交接”：刚完成啥 + 跳过了哪些层
    resume = resume_target(stack)
    if resume is not None:
        resume["status"] = "active"      # 回到的那层此刻就是当前会话（paused/suspended 一律提为 active）
        touch(resume)
        resume["completed_handoff"] = {
            "type": popped.get("type"),
            "doing": (popped.get("doing") or popped.get("why") or "")[:200],
            "skipped": " → ".join(skipped) if skipped else "",
            "label": popped.get("label") or popped.get("context_ref") or "",
            "tail": popped.get("tail") or [],
        }

    await _save(db, agent_id, stack)

    # P4: 自动写 JOURNAL（弹栈帧）
    await _auto_journal(db, agent_id, "pop", popped)

    logger.info(f"Agent({agent_id}) pop [{popped.get('type')}]"
                + (f" 跳过 {len(skipped)} 帧" if skipped else ""))

    if resume is not None:
        suffix = f"（跳过 {len(skipped)} 帧）" if skipped else ""
        return stack, f"已弹出 [{popped.get('type')}]，恢复到 [{resume.get('type')}]: {resume.get('doing', resume.get('why', ''))}{suffix}"
    return stack, f"已弹出 [{popped.get('type')}]，状态栈已空"


def _close_frame(stack: list[dict], frame: dict) -> None:
    """把一帧移出运行集合（标记结束），并把回到的那层提为当前帧。

    只改 status：帧记录留在存储里，等他交接完后事才由 finish_frame 删。
    回到的那层不论原来是 paused（被压着）还是 suspended（会话切走了），此刻它就是当前会话，
    统一提为 active——否则会出现"运行集合里一个 active 都没有"，摘要就没有当前帧可念。
    """
    frame["status"] = ENDED_STATUS
    nxt = resume_target(stack)
    if nxt is not None:
        nxt["status"] = "active"
        touch(nxt)


async def close_state(
    db: AsyncSession, agent_id: int, frame_id: str = "",
) -> tuple[list[dict], str]:
    """关闭指定帧或当前帧：同样只出运行集合（status=ended），**记录保留**。

    frame_id 为空时关当前帧。返回 (全量存储, 消息)。
    """
    db = _ensure_repo(db)
    stack = await _get_stack(db, agent_id)

    live = running(stack)
    if not live:
        return [], "状态栈为空，无需关闭"

    frame = by_id(stack, frame_id) if frame_id else live[-1]
    if frame is None or not is_running(frame):
        return stack, f"未找到状态帧 {frame_id}"
    _close_frame(stack, frame)

    await _save(db, agent_id, stack)
    logger.info(f"Agent({agent_id}) close [{frame.get('type')}]({frame.get('id')})")
    return stack, f"已关闭状态帧 [{frame.get('type')}]"


async def finish_frame(db: AsyncSession, agent_id: int, frame_id: str) -> tuple[bool, str]:
    """他表态「这帧的后事办完了」→ **删帧**。这是帧唯一的删除点。

    为什么判据只有他能给：平台猜不出来（"连续几轮没提它"这类猜测，猜错就是静默丢状态）。
    只销不在运行集合里的帧——正在跑的状态该用 close_state 关，不该被一纸交接单销掉。
    """
    db = _ensure_repo(db)
    stack = await _get_stack(db, agent_id)
    frame = by_id(stack, frame_id)
    if frame is None:
        return False, f"没找到状态帧 {frame_id}"
    if is_running(frame):
        return False, f"状态帧 {frame_id} 还在运行，先用 close_state 关掉它再交接"
    stack = [f for f in stack if f.get("id") != frame_id]
    await _save(db, agent_id, stack)
    logger.info(f"Agent({agent_id}) 帧后事交接完毕，销掉 [{frame.get('type')}]({frame_id})")
    return True, f"已销掉状态帧 [{frame.get('type')}]({frame_id})"


async def handover_snapshot(db: AsyncSession, agent_id: int) -> tuple[list[dict], list[dict]]:
    """待交接帧 + 当前帧上还没投递的代销告知（构建提示词那一步据此落历史）。

    读一次存储就够：待交接帧从全量里筛，代销告知挂在当前帧上。
    """
    db = _ensure_repo(db)
    stack = await _get_stack(db, agent_id)
    pend = retired(stack)
    cur = current(stack)
    notices = list((cur or {}).get("pending_notices") or [])
    return pend, notices


async def clear_pending_notices(db: AsyncSession, agent_id: int) -> int:
    """代销告知已经投成历史条目 → 清掉当前帧上的标记（账本里那份才是凭据）。"""
    db = _ensure_repo(db)
    stack = await _get_stack(db, agent_id)
    cur = current(stack)
    if cur is None or not cur.get("pending_notices"):
        return 0
    n = len(cur["pending_notices"])
    cur["pending_notices"] = []
    await _save(db, agent_id, stack)
    return n


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
    await _save(db, agent_id, stack)
    return copies


async def read_frame_env(db: AsyncSession, agent_id: int, context_ref: str) -> tuple[bool, dict | None, dict | None]:
    """读取本会话帧上的环境状态。

    返回 (established, locked, notified)：established 表示该会话帧是否已建立环境基线；
    locked 是写进前缀的那一份；notified 是已告知 AI 的那一份。会话帧不在栈顶时，
    三者依次为 False、None、None。

    两个字段均不存在即尚未建立基线（新帧，或由旧版本升级而来的帧），调用方据此只记录、
    不通知——否则 AI 进入会话时会先收到一条并不存在的环境变更。
    """
    db = _ensure_repo(db)
    stack = await _get_stack(db, agent_id)
    if not stack or str(stack[-1].get("context_ref")) != str(context_ref):
        return False, None, None
    top = stack[-1]
    if "env_locked" not in top and "env_notified" not in top:
        return False, None, None
    return True, top.get("env_locked"), top.get("env_notified")


async def write_frame_env(db: AsyncSession, agent_id: int, context_ref: str, *,
                          locked: dict | None, notified: dict | None) -> bool:
    """写入本会话帧的环境字段，返回是否命中该会话帧。

    只认栈顶帧即本会话，理由与 set_frame_notes 相同：切换未完成时栈顶仍是别的会话，
    强行写入会把 A 的环境记到 B 名下。本函数不提交事务，以保证与通知条目同属一次提交。
    """
    db = _ensure_repo(db)
    stack = await _get_stack(db, agent_id)
    if not stack or str(stack[-1].get("context_ref")) != str(context_ref):
        return False
    stack[-1]["env_locked"] = locked
    stack[-1]["env_notified"] = notified
    await _save(db, agent_id, stack)
    return True


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
        await _save(db, agent_id, stack)
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
        await _save(db, agent_id, stack)
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
    await _save(db, agent_id, stack)
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
        await _save(db, agent_id, stack)
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

    label = f"{conv_label}「{title}」"
    prev = current(stack)
    # 在**运行集合**里找本会话的帧：已结束的帧不复活（那段上下文已经重新开始了）
    frame = next((f for f in running(stack) if f.get("context_ref") == context_ref), None)

    if frame is not None:
        # 切回挂起的会话：把「刚离开那段对话」的尾巴挂上，切回瞬间才知道刚才在别处说到哪
        if prev is not None and prev is not frame:
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
    else:
        # 新会话：旧当前帧挂起 + push（handoff 写在新帧上——渲染读当前帧）
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

    touch(frame)
    await _save(db, agent_id, stack)


async def touch_active_frame(db: AsyncSession, agent_id: int) -> None:
    """记一次"这个状态被调用了"（决策调用也走这里）。

    last_active_at 是容量超限时挑「最久没被调用」的依据，所以口径必须是**调用**而不是创建时间：
    一个刚建的会话帧可能就是当前帧，而一个天天在用的老会话帧不该因为建得早被挂起。
    """
    db = _ensure_repo(db)
    stack = await _get_stack(db, agent_id)
    top = current(stack)
    if top is None:
        return
    touch(top)
    await _save(db, agent_id, stack)


async def bump_frame_call_count(db: AsyncSession, agent_id: int, calls: int = 1) -> None:
    """LLM 每次调用后：agent 总计数 +1；当前帧 call_count +1 并做情感衰减
    （mood homeostasis——情感随该状态自己的调用次数回归基线），同时刷新 last_active_at。"""
    db = _ensure_repo(db)
    from sqlalchemy import text as _text
    # agent 总计数
    await db.execute(
        _text("UPDATE agents SET llm_call_count = llm_call_count + :c WHERE id = :aid"),
        {"c": calls, "aid": agent_id},
    )
    # 当前帧计数 + 情感衰减 + 刷新"最后一次调用"
    stack = await _get_stack(db, agent_id)
    top = current(stack)
    if top is not None:
        top["call_count"] = int(top.get("call_count") or 0) + calls
        if top.get("emotion"):
            top["emotion"] = decay_emotion(top["emotion"], calls)
        touch(top)
        await _save(db, agent_id, stack)


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
    await _save(db, agent_id, stack)
    return top["emotion"]


async def set_active_emotion_text(db: AsyncSession, agent_id: int, text: str) -> None:
    """设置栈顶帧文字心情（未向量化模式）。"""
    db = _ensure_repo(db)
    stack = await _get_stack(db, agent_id)
    if not stack:
        return
    stack[-1]["emotion_text"] = text[:100]
    await _save(db, agent_id, stack)


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


async def current_context(db: AsyncSession, agent_id: int,
                          fallback_group_id: int | None = None) -> dict:
    """AI 现在在哪：本次回复所在的群 + 状态栈里最近的群/世界（一次扫描）

    世界命令与进世界都要「平台自己记着的事实」（帧就是平台记的），各写一份扫描迟早会分叉。
    返回：
    - group_ids：候选群，按新鲜度排——本次回复所在的群排第一，然后是栈里最近的群会话（去重）；
    - world_id / channel_group_id：栈里最近那条世界帧的世界号与通道群（没进过世界就是 None）。
    """
    groups: list[int] = [int(fallback_group_id)] if fallback_group_id is not None else []
    world_id: int | None = None
    channel_group_id: int | None = None
    for frame in reversed(running(await _get_stack(db, agent_id))):
        ref = str(frame.get("context_ref") or "")
        if ref.startswith("group:"):
            try:
                group = int(ref.split(":", 1)[1])
            except ValueError:
                continue
            if group not in groups:
                groups.append(group)
        elif world_id is None and frame.get("type") == "world" and ref.startswith("world:"):
            try:
                world_id = int(ref.split(":", 1)[1])
            except ValueError:
                continue
            raw_group = frame.get("group_id")
            try:
                channel_group_id = int(raw_group) if raw_group else None
            except (TypeError, ValueError):
                channel_group_id = None
    return {"group_ids": groups, "world_id": world_id, "channel_group_id": channel_group_id}


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
        await _save(db, agent_id, stack)
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
