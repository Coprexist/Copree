"""
状态帧纯函数 — 无 IO、无 DB 依赖。

存储与运行是两层（本模块的分界线）：
- 存储 = 整个数组（含已结束 ended、待交接 retired 的帧，全量保留，只在他交接完后事时删）；
- 链上（running）= status 处于 active/paused/suspended 的那串帧。

链上帧的**顺序**不在这里算：它是状态链（agent_state_chain，见 pure/state_chain.py）的事——
切回某帧是把它拉到链尾，不是重排这个数组。本模块只回答"哪些帧还在跑"，链回答"接下来跑谁"。

make_state_frame(): 构建单个状态帧（交接驱动：handoff/completed_handoff）
normalize_order(): 排成 [历史区][运行区，当前帧在末尾]——全仓 stack[-1] 等于当前帧的依据
current()/running()/by_id()/retired(): 链上与存储的各自视图
frame_of(): 一段会话的那一帧（身份 = context_ref；所有认帧的路径共用这一个口）
merge_same_context(): 链上同一段会话只留一帧（重复帧归并进最近激活的那帧）——写入点的不变量
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
# 默认 31，AI 可自配（update_self_config）；它是**存储**的上限，不是链上的上限。
DEFAULT_FRAME_CAPACITY = 31

# 帧状态分两段：在跑的（在链上占位次）与留在存储的（不再参与运行）。
# 分开表达是这一层的全部要点：pop/close 只把帧从链上取下来，**不删记录**；
# 记录只在「他交接完后事」之后才删（唯一删除点）。
RUNNING_STATUSES = ("active", "paused", "suspended")
ENDED_STATUS = "ended"
RETIRED_STATUS = "retired"
# "不在链上"的历史写法：completed / closed 是旧口径留下的，读的时候一视同仁，
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
    # world_role：世界帧（type=world）的入场身份（绑定类型 slug；None = 只拿通用技能）。
    # 帧不记群：一个世界绑几个群都只有这一条帧，命令直达世界程序——群不是世界的属性，
    # 更不是投递管道（见 services/world/world_frame_service.py 的模块说明）。
    "world_role",
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
    # 帧的轮生命周期（docs/dev/frame_turn_lifecycle.md）：三态 + 额度 + 借贷账
    "turn_state", "steps_left", "lent_out", "borrow_from",
    # semantic_focus：这段会话当前的语义焦段（焦段本体存在 agents.foci）
    "semantic_focus",
    # env_locked：写进前缀的那份环境（含平台的通道规矩；解锁点对齐现值）；
    # env_notified：已告知 AI 的那份（判定基准）。
    # 两者分开是必需的——值变过去又变回来时，现值可能等于 locked 却不等于 notified。
    "env_locked",
    "env_notified",
    # usage：插件帧的调用次数桶（{hour:{key,n}, day:{key,n}}）。放在帧上而不是另建表：
    # 换帧/结束帧时它跟着帧走，compact 也不该把它清零——那是"这个状态花了多少"的事实
    "usage",
    # tool_uses / delivered：触发组合规则的本会话状态（哪个工具调过几次、哪条规则投过了）。
    # 挂在帧上是有意的——compact/clear 后帧重建，「本会话第一次」自然归零，不必另建状态表
    "tool_uses",
    "delivered",
    # roster_locked：写进请求的那份「你手上有哪些帧」清单（解锁点与首次建帧时渲染，
    # 之后字节不动）；roster_notified：已经告知过的那份（{帧 id: 帧身份}，判定增删的基准）。
    # 与 env_locked / env_notified 同一个套路：清单是快照，变化只补条目。
    "roster_locked",
    "roster_notified",
    # dormant：预建好、还没进去过的沉睡帧（插件替 AI 建的）。它不在链上，
    # 但确实是"能进去的一帧"，清单要列它；一旦上过链就不再算沉睡
    "dormant",
    # unread / waiting_since / last_stimulus：有人在别的会话里找他，而他那时不在那儿。
    # 帧挂进链上（suspended）并记未读，由他自己决定什么时候过去（pull 过去即认领、清未读）。
    # 系统不自动切帧——切帧是 AI 的动作，这里只把帧与事实摆到台面上
    "unread",
    "waiting_since",
    "last_stimulus",
)


# 可以被就地改写的文本字段（update_frame 的唯一可写集合）。
# 身份字段（id / type / context_ref / status）不在这里：换身份要走 push / restore，
# 帧 id 是闹钟之类记着的长期指针，不能靠"改一下字段"换人。
FRAME_TEXT_FIELDS = ("label", "why", "doing", "todo", "plan", "journal")


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
        # 轮三态与额度：新帧还没有未完成的轮，额度在第一次被刺激时给出。
        # 额度只存「还剩多少步」——判断/赋值都是一行，不会出现 used/limit 不同步
        "turn_state": "closed",
        "steps_left": 0,
        "lent_out": 0,
        "borrow_from": [],
        # 未读刺激：别的会话里有人找他。去不去由他定，pull 过去才认领
        "unread": 0,
        "waiting_since": "",
        "last_stimulus": {},
    }
    for key, value in extras.items():
        if key in _FRAME_FIELDS and value is not None:
            frame[key] = value
    frame["emotion"] = normalize_emotion(frame["emotion"])
    return frame


def frame_tail(messages: list[dict], max_exchanges: int = 2, max_chars: int = 400) -> list[str]:
    """取「最后几轮对话原文」（切走时靠它保持连续，无需额外 LLM 调用）。

    只认 user/assistant 且有文本内容的消息：system 段（规矩、时间、摘要）不是
    对话原文，混进来只会让 AI 把注入内容当成对方说过的话。按「轮」收集——收集满
    max_exchanges 条用户消息（连同其后的回复）即停；再从最旧端按 max_chars 丢，
    因为尾巴的意义就是「最新说到哪」。

    默认收得很窄（2 轮 / 400 字）：后半段交接交给经历行（那一场干过什么），原文只负责
    「最后一句说到哪」。两个数在 runtime 配置里可调，不必改这里。
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
# 链上（只剩指针排队）与存储的分界
# ═══════════════════════════════════════════════════════════════

def is_running(frame: dict) -> bool:
    """这帧还在链上吗（当前 / 被压着还要回来 / 会话切走了等它回来）。"""
    return (frame or {}).get("status") in RUNNING_STATUSES


def is_ended(frame: dict) -> bool:
    """已出链（ended / 旧写法的 completed、closed）。"""
    return (frame or {}).get("status") in ENDED_STATUSES


def running(stack: list[dict]) -> list[dict]:
    """状态链 —— 数组里"还在跑"的那串帧，保序（相对顺序即排队位次）。

    存储 = 整个数组（含已结束/待交接的帧，永不随手丢）；运行 = 这个过滤视图。
    所以"当前帧"不再是 stack[-1]：数组末尾可能躺着一帧已经结束的。
    """
    return [f for f in (stack or []) if is_running(f)]


def current(stack: list[dict]) -> dict | None:
    """当前帧 = 状态链里 status 为 active 的那个。

    正常情况下状态链里恰好一个 active（切换/弹出/关闭都会把当前帧提上来）。若一个都没有
    （历史数据，或将来某条路径漏了提升），退回链尾**并告警**——兜底可以，静默不行。
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
                "链上没有 active 帧，暂以链尾为当前帧："
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
    """撤下当前帧之后回到哪一层：链尾。

    与 current() 的区别是时机：此刻当前帧刚被标成 ended，链上可能一个 active 都没有，
    这不是异常，而是"正要把它提上来"。所以这里不告警，也不该走 current() 的兜底。
    """
    live = running(stack)
    return live[-1] if live else None


def by_id(stack: list[dict], frame_id: str) -> dict | None:
    """按帧 id 找帧——在**全量存储**里找，所以被 pop/close 过的帧照样找得到。"""
    fid = str(frame_id or "")
    return next((f for f in (stack or []) if fid and f.get("id") == fid), None)


def frame_of(stack: list[dict], context_ref: str) -> dict | None:
    """这段会话的那一帧（身份 = context_ref；没有就返回 None）。

    一段会话只有一帧——身份是"哪段会话"，不是"第几次压帧"。历史库里可能还躺着同名的重复帧
    （旧版本每轮新建一条），所以这里给出确定的挑选规则，而不是撞见哪条算哪条：在跑的优先
    （存储顺序里运行区在末尾，最后那帧就是最近切进来的），全都不在跑时取最近激活的那条——
    该复活的是最近离开的那次，不是更早的某次。

    切会话（ensure_active_frame，只给 AI 自己的动作）、他自己压帧（push_state）、插件代建（enter_frame）都从这里
    认帧，于是不会有一条路径自己另起一帧。
    """
    ref = str(context_ref or "")
    if not ref:
        return None
    same = [frame for frame in (stack or []) if str(frame.get("context_ref") or "") == ref]
    if not same:
        return None
    # 挑选规则与归并共用 _pick_keeper 一处：认帧和"归并时留谁"必须是同一个答案，
    # 否则并发下两边会各自认定不同的那条，归并完再认一次又换人
    return _pick_keeper([frame for frame in same if is_running(frame)] or same)


def touch(frame: dict) -> None:
    """刷新最后一次激活时刻（容量超限时按它挑最久没用的帧）。"""
    if frame is not None:
        frame["last_active_at"] = datetime.now(timezone.utc).isoformat()


def retired(stack: list[dict]) -> list[dict]:
    """待交接的帧（交给他办后事；办完才删）。"""
    return [f for f in (stack or []) if f.get("status") == RETIRED_STATUS]


def _overflow(stack: list[dict], capacity: int) -> list[dict]:
    """帧超容量时该让谁走：**最久没被调用**的非当前帧，够数即止。

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


def world_id_of(frame: dict | None) -> int | None:
    """世界帧 → 世界号；不是世界帧（或 ref 不合法）返回 None。

    「世界帧」= type=world 且 context_ref=world:{id}（进世界时建的那条）。世界号**只**从
    ref 解析、不另设字段：世界帧是"一条状态帧"，帧上任何第二处世界号都会和 ref 分叉。
    """
    if not isinstance(frame, dict) or frame.get("type") != "world":
        return None
    ref = str(frame.get("context_ref") or "")
    if not ref.startswith("world:"):
        return None
    try:
        return int(ref.split(":", 1)[1])
    except ValueError:
        return None


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


# 归并时不属于内容的字段：身份/时序由幸存者自己定，标记只属于被并的那条帧，
# 搬过去会变成"幸存者说自己是被并的"或"幸存者被标成待交接"
_MERGE_SKIP_FIELDS = ("id", "created_at", "status", "last_active_at",
                      "merged_into", "retired_at")


def _merge_frame_into(keeper: dict, frame: dict) -> None:
    """把一个被归并帧并进幸存者（规则表见 merge_same_context 的说明与 docs/dev/frame_lifecycle.md）。"""
    for key, value in frame.items():
        if key in _MERGE_SKIP_FIELDS or _blank(value):
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


def _context_groups(frames: list[dict]) -> list[list[dict]]:
    """按 context_ref 分组的帧（空 ref 不参与：它们不属于任何一段会话）。"""
    groups: dict[str, list[dict]] = {}
    for frame in (frames or []):
        ref = str(frame.get("context_ref") or "")
        if ref:
            groups.setdefault(ref, []).append(frame)
    return list(groups.values())


def _pick_keeper(frames: list[dict]) -> dict:
    """一组重复帧里留谁：当前帧优先，其次最近激活。"""
    return max(frames, key=lambda frame: (
        frame.get("status") == "active",
        str(frame.get("last_active_at") or frame.get("created_at") or ""),
    ))


def _merge_group(frames: list[dict]) -> list[dict]:
    """把一组同会话的帧归并进幸存者，返回被归并的帧（已置 ended、记 merged_into）。

    规则表（契约级，改这里必须同步 docs/dev/frame_lifecycle.md「同一段会话至多一帧」）：
    留「当前帧优先、其次最近激活」那帧；**未列出的字段一律幸存者优先**（同名值有意丢弃）；
    last_active_at 取组内最大（created_at 取幸存者的：它只是容量闸排序的兜底，重建会归零）；
    call_count 求和、tool_uses 逐键取大、delivered 键并集；pending_notices / notes / tail 与
    交接包的 tail 取并集。被归并帧置 ended 并记 merged_into（留痕：不是他自己结束的）。
    """
    if len(frames) < 2:
        return []
    keeper = _pick_keeper(frames)
    merged: list[dict] = []
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


def merge_same_context(stack: list[dict]) -> list[dict]:
    """同一段会话只留一帧：链上同 context_ref 的重复帧归并进一帧，返回被归并的帧。

    重复来自"push 只跟当前帧比身份"（线上实测：某 AI 的 group:59 攒了三帧）。挂起/销掉是事后清
    症状，不变量要落在唯一写入点（_save）——这样哪条路径 push 都攒不出第二帧。

    只看链上的帧：写入点兜的是并发（两个请求同时读到同一条尾），不是历史数据。旧版本留下的
    已结束重复帧由迁移 0109 一次性归并，运行时不再扫它们。
    """
    merged: list[dict] = []
    for frames in _context_groups(running(stack)):
        merged += _merge_group(frames)
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


def handoff_pending(pending: dict | None) -> bool:
    """这份交接还没被消费吗——判据是「有东西可讲」，不要求一定有原文。

    原文尾巴是收窄过的（2 轮 / 400 字），插件帧、纯工具帧可能一条对话原文都没有；
    但「你刚才在[刷 B 站]」本身就是要交接的信息（也是经历按场补投的触发点）。
    """
    p = pending or {}
    if p.get("consumed"):
        return False
    return bool(p.get("tail") or p.get("from_context_ref") or p.get("label")
                or p.get("from_label") or p.get("doing"))


def format_handoff_tail(label: str, tail: list[str], reason: str = "", doing: str = "") -> str:
    """渲染「上一段对话的原文尾巴」——临时性交接，只注入一次。

    两道边界都要说清（2026-09-25 实测踩过第一条的坑）：
    - **别串台**：群里看到私信的尾巴，AI 很容易顺手在群里答一句私信的内容；
    - **但约定要履约**：如果那段对话里说好了「到了别处做什么」（暗号、触发条件、待办），
      必须照做——只写禁令会让 AI 明明看见了暗号也不敢答（用户实测：私信约好暗号，
      群里喊了暗号，AI 只看见"便签：就剩暗号那条待验证"，却没看见答什么，也没敢接）。

    `tail` 为空但 `doing` 有内容时（插件帧、纯工具帧），只讲「你刚才在那边做什么」——
    没有原文可抄，但来源必须说清，否则 AI 不知道自己是从哪段状态里被叫出来的。
    """
    where = f"（{label}）" if label else ""
    if not tail and not doing:
        return ""
    lines = [
        f"## 📎 刚才你在别处{where}",
        "这是你刚离开的那段状态：",
        "- 不要在当前会话里回应它、不要把它当成当前会话的消息，也不要向当前的人复述它；",
        "- 但如果那边和你约定了**到了别处要做的事**（暗号、触发条件、待办），"
        "现在条件满足就按约定执行——这条提醒不构成不做它的理由。",
    ]
    if doing:
        lines.append(f"- 你在那边正在做的事：{doing}")
    if reason:
        lines.append(f"（你离开那里的原因：{reason}）")
    if tail:
        lines.append("最后几句原文：")
        lines += [f"  {t}" for t in tail]
    lines.append(
        "（要是那边还有该带到别处、但这里没说完的临时约定，用 cross_state_note 记下来："
        "40 次 API 调用内有效，别的会话拿到后会一直留在它的上下文里。）"
    )
    return "\n".join(lines)


def format_state_stack_summary(stack: list[dict], max_chars: int = 500,
                               focus_line: str = "", budget_line: str = "") -> str:
    """帧数组 → AI 可读摘要（交接驱动）。

    只渲染「当前帧 + 交接信息」，不逐层展开历史帧：
    - 旧交接已在 LLM 对话历史里出现过（工具调用参数），不重复注入
    - 当前帧 = 状态链里 active 的那个（**不是数组末尾**：末尾可能躺着已结束的帧）
    - 当前帧：doing / TODO / PLAN / 🎭 情感（完整）
    - handoff：本次切换的交接（← 从[来源]来，为什么，回去继续）
    - completed_handoff：pop 回来后刚完成的交接（📝 刚完成）
    - 嵌套提示：链深 > 1 时给"共 N 帧"计数
    - focus_line：当前会话的焦段归属（由调用方按 agents.foci 算好，纯函数只负责渲染）
    - budget_line：插件帧的预算（一轮上限 / 本小时 / 今天 / token 用量，同样由调用方算好）

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
        if budget_line:
            lines.append(f"   预算: {budget_line}")
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
        # 📬 有人在别的会话里找他：帧已经在链上，去不去由他决定（刺激传导）
        waiting = [f for f in stack
                   if f is not top and int(f.get("unread") or 0) > 0]
        for w in waiting[:3]:
            n = int(w.get("unread") or 0)
            stim = w.get("last_stimulus") or {}
            who = stim.get("actor") or ""
            last = " ".join(str(stim.get("summary") or "").split())[:40]
            at = str(stim.get("at") or "")
            clock = at[11:16] if len(at) > 16 else ""
            name = w.get("label") or w.get("context_ref") or "?"
            head = f"   📬 {name} 有 {n} 条未读"
            tail = " · ".join(x for x in ((f"{who}：{last}" if who else last), clock) if x)
            lines.append(f"{head}（{tail}）" if tail else head)
        if waiting:
            lines.append("     要过去就 pop_state(target_frame_id=帧 id，见帧清单)；不去也行，继续手头的事")
        if len(waiting) > 3:
            lines.append(f"   📬 另有 {len(waiting) - 3} 段有未读")
        live = running(stack)
        if len(live) > 1:
            lines.append(f"   ⏸ 另有 {len(live) - 1} 帧未完成（见开头的帧清单）")
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
    """从状态摘要里读回「当时当前帧是哪一帧」——format_state_stack_summary 的逆。

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


def frame_ident(frame: dict | None) -> str:
    """帧身份说人话：`[type] label`；没写 label 的帧退回 context_ref（与摘要同一个口径）。"""
    frame = frame or {}
    type_ = str(frame.get("type") or "?")
    label = str(frame.get("label") or frame.get("context_ref") or "").strip()
    return f"[{type_}] {label}".strip()


ROSTER_MAX_ROWS = 4  # 清单最多列几帧：链很长时（线上见过 126 帧）不能把这段吃光


def roster_frames(stack: list[dict]) -> list[dict]:
    """清单要列的帧：链上的帧 + 预建的沉睡帧（插件替 AI 建好、等着它进去的那些）。

    沉睡帧的 status 是 ended，不在链上；不列出来，AI 就只能自己 push_state 造一帧
    顶上（线上真发生过：「刷 B 站」明明是预建好的，它另建了一个 world 帧）。
    """
    live = running(stack)
    live_ids = {str(f.get("id")) for f in live}
    dormant = [f for f in (stack or [])
               if f.get("dormant") and str(f.get("id")) not in live_ids]
    return live + dormant


def render_frame_roster(stack: list[dict], max_rows: int = ROSTER_MAX_ROWS) -> str:
    """状态帧清单：一次给全「你手上有哪些帧」，省掉每次先 list_states 找 id。

    只列链上的帧（active / paused / suspended）。已结束的帧进不去（进去是复活，得先知道
    为什么进去），待交接的那批只报数——那是后事，不是可选的目的地。

    渲染时机由调用方定：解锁点与「这一帧第一次成为当前帧」时渲染一次，之后字节不动，
    增删只补账本条目（见 services/agent/state_stack_service.frame_roster）。
    """
    frames = roster_frames(stack)
    if not frames:
        return ""
    lines = ["## 🗂 你手上的状态帧"]
    for frame in frames[:max_rows]:
        mark = "" if is_running(frame) else "（沉睡）"
        lines.append(f"- {frame_ident(frame)}{mark} id={frame.get('id')}")
    if (rest := len(frames) - max_rows) > 0:
        lines.append(f"- …另有 {rest} 帧，调 list_states 看全量")
    lines.append("要进哪一帧就 enter_state(id)；新建、进出、结束都会在后面补一条。")
    return "\n".join(lines)


def is_state_summary(content: Any) -> bool:
    """这条 system 消息是不是状态摘要块。

    注入块的首字节固定：用户自己贴一段带同样标记的文字不算（它是消息正文，不是摘要）。
    """
    return isinstance(content, str) and content.startswith(_STATE_SUMMARY_PREFIX)


def state_frame_of(messages: list[dict]) -> dict:
    """从一份请求体里读回当时的状态帧身份（那轮没注入状态摘要时返回空）。

    只认开头就是标记的 system 消息：注入块的首字节固定，用户自己贴一段带同样标记的
    文字不会被当成状态。
    """
    for msg in messages or []:
        if not isinstance(msg, dict) or msg.get("role") != "system":
            continue
        if is_state_summary(msg.get("content")):
            return parse_state_summary(msg["content"])
    return {}

