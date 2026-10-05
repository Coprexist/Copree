"""
状态帧纯函数 — 无 IO、无 DB 依赖。

存储与运行是两层（本模块的分界线）：
- 存储 = 整个数组（含已结束 ended、待交接 retired 的帧，全量保留，只在他交接完后事时删）；
- 运行集合 = running(stack)，status 处于 active/paused/suspended 的那串指针，"栈"只剩它。

make_state_frame(): 构建单个状态帧（交接驱动：handoff/completed_handoff）
normalize_order(): 排成 [历史区][运行区，当前帧在末尾]——全仓 stack[-1] 等于当前帧的依据
current()/running()/by_id()/retired(): 运行集合与存储的各自视图
merge_same_context(): 同一段会话只留一帧（重复帧归并进最近激活的那帧）——写入点的不变量
retire_overflow()/drop_overflow()/drop_retired_overflow(): 容量闸的两条走法（挂起待交接 / 平台直接代销）与积压硬底
format_state_stack_summary(): 存储 → AI 可读摘要（只渲染当前帧 + 交接信息 + 待交接计数）
parse_state_summary(): 摘要 → 当时的当前帧身份（写与读共用一份标记，见 STATE_SUMMARY_MARK）

情感向量纯函数见 emotion.py（独立模块）。

契约（状态、容量闸、同会话归并）：docs/dev/frame_lifecycle.md。
"""
from __future__ import annotations

from datetime import datetime, timezone
import logging
import re
import uuid

logger = logging.getLogger(__name__)

from app.utils.pure.emotion import (
    normalize_emotion, emotion_to_text,
)


# 帧容量：存储里帧数超过它，就把「最久没被激活」的那个帧挂起交给他自己办后事。
# 默认 31，AI 可自配（update_self_config）；它是**存储**的上限，不是运行集合的上限。
DEFAULT_FRAME_CAPACITY = 31

# 帧状态分两段：在跑的（占运行集合=指针排队）与留在存储的（不再参与运行）。
# 分开表达是这一层的全部要点：pop/close 只把帧从运行集合里取出来，**不删记录**；
# 记录只在「他交接完后事」之后才删（唯一删除点）。
RUNNING_STATUSES = ("active", "paused", "suspended")
ENDED_STATUS = "ended"
RETIRED_STATUS = "retired"
# "不在运行集合"的历史写法：completed / closed 是旧口径留下的，读的时候一视同仁，
# 新写一律用 ended——三种写法并存会让"这帧还在跑吗"这种问题每次都要想一遍。
ENDED_STATUSES = (ENDED_STATUS, "completed", "closed")

# 容量硬底：挂起待交接的帧也不能无限堆积（他若一直不交接）。超过 capacity × 这个倍数，
# 平台代销最旧的那批——代销是平台替他删的，必留一条账本条目，有据可查，不静默。
# （不接手后事那一档压根不挂起，超容量直接代销，见 drop_overflow。）
RETIRE_BACKLOG_FACTOR = 2

# 状态摘要的写与读共用这一份标记：写入在 format_state_stack_summary，读回在 parse_state_summary。
# 两边各留一份字面量的话，改一处就会让日志那边悄悄读不出状态——按状态分组全靠它。
STATE_SUMMARY_MARK = "## 📋 当前状态"
_STATE_SUMMARY_PREFIX = "\n\n" + STATE_SUMMARY_MARK
# 摘要首行：{▸▶ 活跃 | ▸ 挂起} [type] (label): 在干嘛
_STATE_FRAME_LINE = re.compile(r"^[▸▶]+\s*\[([^\]]*)\]\s*(?:\(([^)]*)\))?")

# 帧的合法扩展字段（make_state_frame 白名单）
_FRAME_FIELDS = (
    "id", "type", "context_ref", "label", "why", "doing", "todo", "plan", "journal",
    "created_at", "status", "emotion", "emotion_text", "source_emotion",
    "tools", "skills", "call_count", "handoff", "completed_handoff",
    # group_id：世界帧（type=world）的通道群——命令要落到一个具体的群，世界本身不记这个。
    # 一个世界可能绑好几个群（世界 36 → 群 24/48/49/50/51），所以群必须跟着帧走。
    "group_id",
    # last_active_at：帧最后一次被**调用**的时刻（LLM 调用与决策调用都算）。容量超限时据此挑
    # 「最久没被调用」的帧，不按创建时间——长期在用的会话帧不该因为建得早就被挂起
    "last_active_at",
    # pending_notices：该告诉他、但还没落进历史的事实（目前是"平台代销了哪几帧"）。
    # 落历史由构建提示词那一步统一做（一次性条目、幂等），这里只记事实
    "pending_notices",
    # tail：这段会话的最后几轮原文。切走时它是「原文尾巴」，切回来时一次性注入
    "tail",
    # notes：投递进这段会话的跨状态便签副本（固化在前缀里，直到 compact/clear）
    "notes",
    # semantic_focus：这段会话当前的语义焦段（焦段本体存在 agents.foci）
    "semantic_focus",
    # env_locked：写进前缀的那份环境（含平台的通道规矩；解锁点对齐现值）；
    # env_notified：已告知 AI 的那份（判定基准）。
    # 两者分开是必需的——值变过去又变回来时，现值可能等于 locked 却不等于 notified。
    "env_locked",
    "env_notified",
    # tool_uses / delivered：触发组合规则的本会话状态（哪个工具调过几次、哪条规则投过了）。
    # 挂在帧上是有意的——compact/clear 后帧重建，「本会话第一次」自然归零，不必另建状态表
    "tool_uses",
    "delivered",
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
        "tool_uses": {},
        "delivered": {},
        "handoff": {},
        "completed_handoff": {},
        "last_active_at": datetime.now(timezone.utc).isoformat(),
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


# ═══════════════════════════════════════════════════════════════
# 运行集合（"栈"只剩指针排队）与存储的分界
# ═══════════════════════════════════════════════════════════════

def is_running(frame: dict) -> bool:
    """这帧还在运行集合里吗（当前 / 被压着还要回来 / 会话切走了等它回来）。"""
    return (frame or {}).get("status") in RUNNING_STATUSES


def is_ended(frame: dict) -> bool:
    """已出运行集合（ended / 旧写法的 completed、closed）。"""
    return (frame or {}).get("status") in ENDED_STATUSES


def running(stack: list[dict]) -> list[dict]:
    """运行集合 —— 数组里"还在跑"的那串指针，保序（相对顺序即排队位次）。

    存储 = 整个数组（含已结束/待交接的帧，永不随手丢）；运行 = 这个过滤视图。
    所以"栈顶"不再是 stack[-1]：数组末尾可能躺着一帧已经结束的。
    """
    return [f for f in (stack or []) if is_running(f)]


def current(stack: list[dict]) -> dict | None:
    """当前帧 = 运行集合里 status 为 active 的那个。

    正常情况下运行集合里恰好一个 active（切换/弹出/关闭都会把当前帧提上来）。若一个都没有
    （历史数据，或将来某条路径漏了提升），退回运行集合末位**并告警**——兜底可以，静默不行。
    """
    live = running(stack)
    if not live:
        return None
    act = next((f for f in reversed(live) if f.get("status") == "active"), None)
    if act is None:
        # 只有一帧在跑时"它不是 active"是正常的（比如日志里那帧已被挂起，但身份还要读得出来），
        # 不当异常；两帧以上都没有 active 才是真不清楚谁是当前——那种要响。
        if len(live) > 1:
            logger.warning(
                "运行集合里没有 active 帧，暂以末位为当前帧："
                + str([(f.get("status"), f.get("context_ref") or f.get("type")) for f in live]))
        return live[-1]
    return act


def normalize_order(stack: list[dict]) -> list[dict]:
    """把数组排成「[历史区][运行区，当前帧在末尾]」——存储顺序的唯一不变量。

    为什么需要它：存储改成"全量保留"之后，数组末尾可能躺着一帧已经结束的，
    而全仓有几十处把 stack[-1] 当"当前帧"用。与其改几十处，不如让**写入/读取各收口一次**
    （state_stack_service 的 _get_stack / _save 都过这里），于是 stack[-1] 恒等于当前帧，
    既不用改那些调用点，也不会有"某一处忘了改"的半吊子状态。
    """
    frames = list(stack or [])
    return (
        [f for f in frames if not is_running(f)]
        + [f for f in frames if is_running(f) and f.get("status") != "active"]
        + [f for f in frames if f.get("status") == "active"]
    )


def resume_target(stack: list[dict]) -> dict | None:
    """撤下当前帧之后回到哪一层：运行集合的末位。

    与 current() 的区别是时机：此刻当前帧刚被标成 ended，运行集合里可能一个 active 都没有，
    这不是异常，而是"正要把它提上来"。所以这里不告警，也不该走 current() 的兜底。
    """
    live = running(stack)
    return live[-1] if live else None


def by_id(stack: list[dict], frame_id: str) -> dict | None:
    """按帧 id 找帧——在**全量存储**里找，所以被 pop/close 过的帧照样找得到。"""
    fid = str(frame_id or "")
    return next((f for f in (stack or []) if fid and f.get("id") == fid), None)


def touch(frame: dict) -> None:
    """刷新最后一次激活时刻（容量超限时按它挑最久没用的帧）。"""
    if frame is not None:
        frame["last_active_at"] = datetime.now(timezone.utc).isoformat()


def retired(stack: list[dict]) -> list[dict]:
    """待交接的帧（交给他办后事；办完才删）。"""
    return [f for f in (stack or []) if f.get("status") == RETIRED_STATUS]


def _overflow(stack: list[dict], capacity: int) -> list[dict]:
    """运行集合超容量时该让谁走：**最久没被调用**的非当前帧，够数即止。

    "最久"的键是 last_active_at（最后一次调用，LLM 调用与决策调用都记），不是创建时间——
    长期在用的会话帧不该因为建得早被挑走。当前帧永远不挑；已挂起的不重复挑。
    挂起与代销（下面两个函数）只差"走的时候留不留记录"，挑选规则必须是同一套，
    所以收在这里一份。
    """
    live = [f for f in running(stack) if not f.get("retired_at")]
    if len(live) <= capacity:
        return []
    cands = [f for f in live if f.get("status") != "active"]
    cands.sort(key=lambda f: str(f.get("last_active_at") or f.get("created_at") or ""))
    return cands[: len(live) - capacity]


def retire_overflow(stack: list[dict], capacity: int) -> list[str]:
    """超容量的帧**挂起待交接**（留记录，等他表态），返回其 id 列表。

    这是"他自己接手后事"那一档的走法：记录留着，下段上下文收到待交接告知，
    办完调 finish_frame 才删。挑不出（全是当前/已挂起）就不动——
    他还没交接，硬删记录是不行的（那正是这一层要修的病）。
    """
    out: list[str] = []
    for frame in _overflow(stack, capacity):
        frame["status"] = RETIRED_STATUS
        frame["retired_at"] = datetime.now(timezone.utc).isoformat()
        out.append(str(frame.get("id") or ""))
    return out


def drop_overflow(stack: list[dict], capacity: int) -> list[dict]:
    """超容量的帧**直接销掉**（平台代销），返回被销的帧。

    用在"这档 AI 不接手帧后事"的时候（预设开关关着）：既然没人来交接，留着记录只是
    占地方，平台代为清理并留一条告知（调用方负责落账本条目，代销必须有据可查）。
    """
    dropped = _overflow(stack, capacity)
    for frame in dropped:
        stack.remove(frame)
    return dropped


def context_frames(stack: list[dict], context_ref: str) -> list[dict]:
    """某个会话名下**还在跑的**帧（会话本身消失了，它们再也跑不起来）。"""
    ref = str(context_ref or "")
    return [f for f in running(stack) if ref and f.get("context_ref") == ref]


def _blank(value) -> bool:
    """这个字段算「没写过」吗（归并时只在空白处补）——空串与空容器都算没写。"""
    return value is None or value == "" or value == {} or value == []


def _union_list(a, b) -> list:
    """并集保序去重——通知 / 便签 / 尾巴这类"事实清单"用。"""
    out = list(a or [])
    for item in (b or []):
        if item not in out:
            out.append(item)
    return out


def _merge_handoff(keeper_handoff, other_handoff) -> dict:
    """交接包：from_* 这类字段幸存者优先，"原文尾巴"取并集——一次性交接不该因为归并丢掉。"""
    out = dict(keeper_handoff or {})
    for key, value in (other_handoff or {}).items():
        if key == "tail":
            out["tail"] = _union_list(out.get("tail"), value)
        elif _blank(out.get(key)):
            out[key] = value
    return out


def _merge_frame_into(keeper: dict, frame: dict) -> None:
    """把一个被归并帧并进幸存者（规则表见 merge_same_context 的说明与 docs/dev/frame_lifecycle.md）。"""
    for key, value in frame.items():
        if key in ("id", "created_at", "status", "last_active_at") or _blank(value):
            continue
        if key == "call_count":
            keeper[key] = int(keeper.get(key) or 0) + int(value or 0)
        elif key == "tool_uses":
            cur = dict(keeper.get(key) or {})
            for k, v in (value or {}).items():
                cur[k] = max(int(cur.get(k) or 0), int(v or 0))
            keeper[key] = cur
        elif key == "delivered":
            cur = dict(keeper.get(key) or {})
            for k, v in (value or {}).items():
                cur.setdefault(k, v)
            keeper[key] = cur
        elif key in ("pending_notices", "notes", "tail"):
            keeper[key] = _union_list(keeper.get(key), value)
        elif key in ("handoff", "completed_handoff"):
            keeper[key] = _merge_handoff(keeper.get(key), value)
        elif _blank(keeper.get(key)):
            keeper[key] = value


def merge_same_context(stack: list[dict]) -> list[dict]:
    """同一段会话只留一帧：运行集合里同 context_ref 的重复帧归并进一帧，返回被归并的帧。

    重复来自"push 只跟栈顶比身份"（线上实测：某 AI 的 group:59 攒了三帧）。挂起/销掉是事后清
    症状，不变量要落在唯一写入点（_save）——这样哪条路径 push 都攒不出第二帧。

    规则表（契约级，改这里必须同步 docs/dev/frame_lifecycle.md「同一段会话至多一帧」）：
    留「当前帧优先、其次最近激活」那帧；**未列出的字段一律幸存者优先**（同名值有意丢弃）；
    last_active_at 取组内最大（created_at 取幸存者的：它只是容量闸排序的兜底，重建会归零）；
    call_count 求和、tool_uses 逐键取大、delivered 键并集；pending_notices / notes / tail 与
    交接包的 tail 取并集。被归并帧置 ended 并记 merged_into（留痕：不是他自己结束的）。
    """
    groups: dict[str, list[dict]] = {}
    for frame in running(stack):
        ref = str(frame.get("context_ref") or "")
        if ref:
            groups.setdefault(ref, []).append(frame)
    merged: list[dict] = []
    for frames in groups.values():
        if len(frames) < 2:
            continue
        keeper = max(frames, key=lambda f: (
            f.get("status") == "active",
            str(f.get("last_active_at") or f.get("created_at") or ""),
        ))
        for frame in frames:
            if frame is keeper:
                continue
            _merge_frame_into(keeper, frame)
            frame["status"] = ENDED_STATUS
            frame["merged_into"] = str(keeper.get("id") or "")
            merged.append(frame)
        # 刚被用过的那一帧，时间戳不该停在旧值上（容量闸按它挑"最久没用"）
        stamps = [str(f.get("last_active_at") or f.get("created_at") or "") for f in frames]
        if max(stamps):
            keeper["last_active_at"] = max(stamps)
    return merged


def retire_context_frames(stack: list[dict], context_ref: str) -> list[str]:
    """把一个会话名下的帧挂起待交接（会话没了：群解散等），返回其 id 列表。

    与容量闸的区别是它不问容量、不等他表态：会话已经不存在，帧留着也只是等他处置。
    当前帧同样要摘——它指的是一个再也回不去的会话，留着会让摘要把死会话当成"正在进行"。
    """
    out: list[str] = []
    for frame in context_frames(stack, context_ref):
        frame["status"] = RETIRED_STATUS
        frame["retired_at"] = datetime.now(timezone.utc).isoformat()
        out.append(str(frame.get("id") or ""))
    return out


def drop_context_frames(stack: list[dict], context_ref: str) -> list[dict]:
    """把一个会话名下的帧直接销掉（这档 AI 不接手帧后事），返回被销的帧。"""
    dropped = context_frames(stack, context_ref)
    for frame in dropped:
        stack.remove(frame)
    return dropped


def drop_retired_overflow(stack: list[dict], capacity: int,
                          factor: int = RETIRE_BACKLOG_FACTOR) -> list[dict]:
    """挂起待交接的帧也不能无限堆积：超过 capacity × factor，平台代销最旧的那批。

    返回被代销的帧（调用方据它留一条「平台代销」的账本条目——代销必须有据可查）。
    排序键是 last_active_at（最后一次调用，LLM 调用与决策调用都算），不是创建时间。
    只销**挂起**的帧：还在跑的、以及没挂起的已结束帧不动（后者等他主动交接）。
    """
    pend = retired(stack)
    limit = max(1, int(capacity or 0)) * max(2, int(factor or 2))
    if len(pend) <= limit:
        return []
    pend.sort(key=lambda f: str(f.get("last_active_at") or f.get("created_at") or ""))
    dropped = pend[: len(pend) - limit]
    for frame in dropped:
        stack.remove(frame)
    return dropped


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


def format_state_stack_summary(stack: list[dict], max_chars: int = 500,
                               focus_line: str = "") -> str:
    """栈 → AI 可读摘要（交接驱动）。

    只渲染「当前帧 + 交接信息」，不逐层展开历史帧：
    - 旧交接已在 LLM 对话历史里出现过（工具调用参数），不重复注入
    - 当前帧 = 运行集合里 active 的那个（**不是数组末尾**：末尾可能躺着已结束的帧）
    - 当前帧：doing / TODO / PLAN / 🎭 情感（完整）
    - handoff：本次切换的交接（← 从[来源]来，为什么，回去继续）
    - completed_handoff：pop 回来后刚完成的交接（📝 刚完成）
    - 嵌套提示：栈深 > 1 时给"共 N 帧"计数
    - focus_line：当前会话的焦段归属（由调用方按 agents.foci 算好，纯函数只负责渲染）

    长度控制（max_chars 默认 500）：超限按降级阶梯（_RENDER_*），
    最新帧的 TODO/PLAN 永不丢。
    """
    top = current(stack)
    if top is None:
        return ""

    def render_top() -> list[str]:
        lines = [_STATE_SUMMARY_PREFIX]
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
        if focus_line:
            lines.append(f"   焦段: {focus_line}")
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
        live = running(stack)
        if len(live) > 1:
            lines.append(f"   ⏸ 另有 {len(live) - 1} 帧未完成（可 list_states 查看）")
        # 待交接帧只报"有几帧、要用哪个工具销"，清单（要处置哪些记忆）落历史，不在这里念
        if pend := retired(stack):
            lines.append(f"   ⏳ 待交接 {len(pend)} 帧（状态已结束，后事办完调 finish_frame 销掉）")
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


def parse_state_summary(summary: str) -> dict:
    """从状态摘要里读回「当时栈顶是哪一帧」——format_state_stack_summary 的逆。

    帧身份 = type + label（没有 label 时是 context_ref）。摘要里本来就没有帧实例 id：
    日志该按「同一段状态」归堆，而不是按「同一次 push」——pop 再 push 是同一段状态。
    """
    if not summary or STATE_SUMMARY_MARK not in summary:
        return {}
    body = summary.split(STATE_SUMMARY_MARK, 1)[1]
    for line in body.splitlines():
        match = _STATE_FRAME_LINE.match(line.strip())
        if match:
            return {"type": match.group(1), "label": match.group(2) or ""}
    return {}


def state_key_of(frame: dict | None) -> str:
    """帧身份的编码：`type|label`（无状态帧时空串）。

    与前端 components/shared/LogState.tsx 的 stateKeyOf 同一口径——它同时是列表分组键与
    URL 参数。日志表把它存成一列（写日志时算一次），裁剪就能按状态分桶而不必回读 messages。
    """
    if not frame or not frame.get("type"):
        return ""
    return f"{frame['type']}|{frame.get('label') or ''}"


def frame_of_state_key(key: str | None) -> dict:
    """state_key_of 的逆；空串还原成空帧（与 state_frame_of 读不到时同形）。"""
    if not key:
        return {}
    at = key.find("|")
    frame_type = key if at < 0 else key[:at]
    label = "" if at < 0 else key[at + 1:]
    return {"type": frame_type, "label": label} if frame_type else {}


def state_frame_of(messages: list[dict]) -> dict:
    """从一份请求体里读回当时的状态帧身份（那轮没注入状态摘要时返回空）。

    只认开头就是标记的 system 消息：注入块的首字节固定，用户自己贴一段带同样标记的
    文字不会被当成状态。
    """
    for msg in messages or []:
        if not isinstance(msg, dict) or msg.get("role") != "system":
            continue
        content = msg.get("content")
        if isinstance(content, str) and content.startswith(_STATE_SUMMARY_PREFIX):
            return parse_state_summary(content)
    return {}

