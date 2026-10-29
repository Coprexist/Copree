"""
状态链纯函数 — 无 IO、无 DB 依赖。

状态链 = 「运行集合」的显式结构：一条**双向链表**，节点只存 id（节点号 → 帧 id，
以及前/后节点号），帧本身仍躺在 agents.state_stack 里。链头是压得最久、最不急的那端，
**链尾是最后加入、正在执行的那端**——只执行链尾，这就是"栈"的 LIFO 语义。

为什么要一条链，而不是继续靠数组顺序：

- **只执行最末位**：执行窗口 = 链尾起 max_active 个节点（默认 1，将来每 AI 可配）。
  谁是当前帧不再靠"数组末尾恰好是谁"，而是 tail 一个指针；
- **切回某帧 = 把它拉到链尾**：常数条指针更新（前后帧相连、接到旧尾之后），
  不必重排整个数组；帧越多，省得越多（线上见过 126 帧 / 546 KB 的一份栈）；
- **帧 id → 节点号 O(1)**：`index` 表就是「算法知道帧的 id 和对应链的 id」，
  AI 提到哪一帧都直接改那个节点，不用扫数组；
- **节点只存 id**：链上不复制帧内容。帧内容改了（doing/todo/情感/计数）不会让链失效，
  链改了也不会重写帧——两层的写放大各归各的。

本模块只管指针算术，**返回要落库的补丁**（`[{id, prev, next}]`），由服务层照着 UPDATE
对应行。于是"链表怎么连"只在这里写一遍，存储层只负责把补丁打到行上。

不变量（`problems()` 逐条核）：
1. head.prev 与 tail.next 都是 None；
2. 每个节点的 prev/next 双向一致（a.next == b 则 b.prev == a）；
3. 从 head 顺走一遍能走完所有节点（无环、无断头）；
4. 一个帧只有一个节点（`index` 与 `nodes` 一一对应）。
违反不变量不静默：服务层对账（reconcile）会修，并留日志。
"""
from __future__ import annotations

import logging

from app.utils.pure.state_stack import is_running

logger = logging.getLogger(__name__)

# 执行窗口：链尾起算的并发帧数。默认 1 = 只跑最后压进来的那帧（与改造前完全一致）。
DEFAULT_MAX_ACTIVE = 1
# 上限：一档 AI 同时跑几十帧既没算力也没意义，卡住免得配错把整条链都算成"在跑"。
MAX_ACTIVE_CAP = 8


# ═══════════════════════════════════════════════════════════════
# 值对象
# ═══════════════════════════════════════════════════════════════

def clamp_max_active(value) -> int:
    """执行窗口取值收口：非法/0 回落到 1，超上限截到 MAX_ACTIVE_CAP。"""
    try:
        n = int(value or 0)
    except (TypeError, ValueError):
        n = 0
    return min(max(1, n), MAX_ACTIVE_CAP)


def empty_chain(max_active: int = DEFAULT_MAX_ACTIVE) -> dict:
    """一条空链。

    nodes: 节点号 → {"frame": 帧 id, "prev": 前节点号, "next": 后节点号}
    index: 帧 id → 节点号（`index` 是 nodes 的逆，attach/detach 里同步维护；
           move_to_tail 不动帧，所以不用碰它）
    """
    return {
        "head": None,
        "tail": None,
        "max_active": clamp_max_active(max_active),
        "nodes": {},
        "index": {},
    }


def chain_size(chain: dict) -> int:
    return len((chain or {}).get("nodes") or {})


def node_of_frame(chain: dict, frame_id: str) -> int | None:
    """帧 id → 节点号（O(1)）。这就是"算法知道帧的 id 和对应链的 id"。"""
    return (chain or {}).get("index", {}).get(str(frame_id or ""))


def frame_of_node(chain: dict, node_id: int) -> str:
    node = (chain or {}).get("nodes", {}).get(node_id)
    return str(node.get("frame") or "") if node else ""


def reindex(chain: dict) -> dict:
    """按 nodes 重建 index（对账前的自愈；正常路径由 attach/detach 维护）。"""
    chain["index"] = {
        str(node.get("frame") or ""): node_id
        for node_id, node in (chain.get("nodes") or {}).items()
        if node.get("frame")
    }
    return chain


def from_rows(chain_row, node_rows) -> dict:
    """存储行的三列 + 节点行的四列 → 值对象。

    chain_row / node_rows 接受"列元组"或"已有属性名的行"，两种写法都认：
    服务层用 `select(...)` 出来的 Row 是前者，测试里直接造 dict 是后者。
    """
    def pick(row, name: str, index: int):
        if row is None:
            return None
        if isinstance(row, dict):
            return row.get(name)
        return row[index]

    chain = empty_chain(pick(chain_row, "max_active", 2))
    chain["head"] = pick(chain_row, "head_id", 0)
    chain["tail"] = pick(chain_row, "tail_id", 1)
    for row in node_rows or ():
        node_id = pick(row, "id", 0)
        chain["nodes"][node_id] = {
            "frame": str(pick(row, "frame_id", 1) or ""),
            "prev": pick(row, "prev_id", 2),
            "next": pick(row, "next_id", 3),
        }
    reindex(chain)
    return chain


# ═══════════════════════════════════════════════════════════════
# 指针算术：每个算子都只改常数个节点，返回要落库的补丁
# ═══════════════════════════════════════════════════════════════

def _patch(chain: dict, node_id: int) -> dict:
    """一个节点"要落库的样子"——整行 prev/next 快照，存储层照抄即可（幂等）。"""
    node = chain["nodes"][node_id]
    return {"id": node_id, "prev": node.get("prev"), "next": node.get("next")}


def _patches(patches: list[dict]) -> list[dict]:
    """同一节点被改多次只留最后一次（move_to_tail 里一个节点可能先断后连）。"""
    latest: dict[int, dict] = {}
    for patch in patches:
        latest[patch["id"]] = patch
    return list(latest.values())


def attach_tail(chain: dict, node_id: int, frame_id: str) -> list[dict]:
    """把**新节点**接到链尾（帧第一次进运行集合时用）。

    node_id 由存储层分配（自增），本函数只认"它是这个号"；节点刚插入时指针为空，
    这里补上 prev=旧尾、next=None，并把旧尾的 next 指过来。
    帧已经有节点是调用方的错（先 detach 或直接 move_to_tail），抛错不静默——
    静默覆盖会把链拧断。
    """
    fid = str(frame_id or "")
    if node_id in chain["nodes"]:
        raise ValueError(f"链节点 {node_id} 已存在")
    if node_of_frame(chain, fid) is not None:
        raise ValueError(f"帧 {fid} 已有链节点 {node_of_frame(chain, fid)}，不该重复挂")

    old_tail = chain["tail"]
    chain["nodes"][node_id] = {"frame": fid, "prev": old_tail, "next": None}
    chain["index"][fid] = node_id
    patches = []
    if old_tail is not None:
        chain["nodes"][old_tail]["next"] = node_id
        patches.append(_patch(chain, old_tail))
    else:
        chain["head"] = node_id
    chain["tail"] = node_id
    patches.append(_patch(chain, node_id))
    return _patches(patches)


def detach(chain: dict, node_id: int) -> list[dict]:
    """把节点从链上摘掉（帧出运行集合：pop / close / 挂起 / 被代销 / 归并）。

    摘掉就是把它的前后帧直接相连；它原本在头或尾就顺手挪指针。节点行由调用方删。
    """
    node = chain["nodes"].pop(node_id, None)
    if node is None:
        return []
    prev, nxt = node.get("prev"), node.get("next")
    fid = str(node.get("frame") or "")
    if chain["index"].get(fid) == node_id:
        chain["index"].pop(fid, None)

    patches = []
    if prev is not None:
        chain["nodes"][prev]["next"] = nxt
        patches.append(_patch(chain, prev))
    else:
        chain["head"] = nxt
    if nxt is not None:
        chain["nodes"][nxt]["prev"] = prev
        patches.append(_patch(chain, nxt))
    else:
        chain["tail"] = prev
    return _patches(patches)


def move_to_tail(chain: dict, node_id: int) -> list[dict]:
    """把链上**已有的**节点拉到最末（"切回这个会话" / "闹钟唤醒这一帧"）。

    正是"只执行最末位"的全部实现，两步：
    ① 让位——此帧的前后帧直接相连（原头被拉走则头后移）；
    ② 落座——上一个最末帧的 next 记成本帧，本帧 prev 记成上一个最末帧，next 置空。
    已经在链尾就什么都不用做（幂等）。
    """
    if node_id not in chain["nodes"]:
        return []
    old_tail = chain["tail"]
    if old_tail == node_id:
        return []

    prev, nxt = chain["nodes"][node_id].get("prev"), chain["nodes"][node_id].get("next")
    patches = []
    # ① 前后帧相连：本帧从原位让开
    if prev is not None:
        chain["nodes"][prev]["next"] = nxt
        patches.append(_patch(chain, prev))
    else:
        chain["head"] = nxt
    if nxt is not None:
        chain["nodes"][nxt]["prev"] = prev
        patches.append(_patch(chain, nxt))
    # ② 接到上一个最末帧之后
    chain["nodes"][node_id]["prev"] = old_tail
    chain["nodes"][node_id]["next"] = None
    if old_tail is not None:
        chain["nodes"][old_tail]["next"] = node_id
        patches.append(_patch(chain, old_tail))
    chain["tail"] = node_id
    patches.append(_patch(chain, node_id))
    return _patches(patches)


def set_max_active(chain: dict, value) -> int:
    """改执行窗口大小（每 AI 可配的那一档）。只改元数据，不动指针。"""
    chain["max_active"] = clamp_max_active(value)
    return chain["max_active"]


# ═══════════════════════════════════════════════════════════════
# 读视图：当前执行帧 / 队列
# ═══════════════════════════════════════════════════════════════

def queue(chain: dict) -> list[int]:
    """整条队列的节点号，**尾在前**（尾 = 先执行的）。O(n)，本来就要列 n 个。

    带环保护：真被拧成环也只会走一圈就停，不在这里空转（`problems()` 会报）。
    """
    out: list[int] = []
    seen: set[int] = set()
    cur = (chain or {}).get("tail")
    nodes = (chain or {}).get("nodes") or {}
    while cur is not None and cur in nodes and cur not in seen:
        out.append(cur)
        seen.add(cur)
        cur = nodes[cur].get("prev")
    return out


def window(chain: dict) -> list[int]:
    """执行窗口：链尾起 max_active 个节点，尾在前。O(max_active)，默认就是 1 个。"""
    limit = max(1, int((chain or {}).get("max_active") or DEFAULT_MAX_ACTIVE))
    out: list[int] = []
    seen: set[int] = set()
    nodes = (chain or {}).get("nodes") or {}
    cur = (chain or {}).get("tail")
    while cur is not None and cur in nodes and cur not in seen and len(out) < limit:
        out.append(cur)
        seen.add(cur)
        cur = nodes[cur].get("prev")
    return out


def executing_frames(chain: dict) -> list[str]:
    """正在执行的帧 id，尾在前（窗口大小 1 时就是"当前帧"那一个）。"""
    return [frame_of_node(chain, node_id) for node_id in window(chain)]


def queue_frames(chain: dict) -> list[str]:
    """整条队列的帧 id，尾在前——"接下来依次要跑谁"的答案。"""
    return [frame_of_node(chain, node_id) for node_id in queue(chain)]


def frames_by_chain(chain: dict) -> list[str]:
    """整条队列的帧 id，**头在前**（存储顺序用：历史区在前、当前帧在末尾）。"""
    return list(reversed(queue_frames(chain)))


def order_by_chain(stack: list[dict], chain: dict | None) -> list[dict]:
    """按链重排整个存储数组：[历史区][运行区，当前帧在末尾]。

    这是全仓 `stack[-1] == 当前帧` 这条老不变量的新依据：顺序不再从 status 推，
    而是链说了算。链里没有的运行中帧（老数据、或某条路径漏挂）接在运行区末尾，
    不丢帧——对账会把它们挂上。
    """
    frames = list(stack or [])
    if not chain or not chain.get("nodes"):
        return frames
    at = {frame_id: pos for pos, frame_id in enumerate(frames_by_chain(chain))}
    history: list[dict] = []
    live: list[tuple[int, dict]] = []
    for frame in frames:
        if not is_running(frame):
            history.append(frame)
            continue
        pos = at.get(str(frame.get("id") or ""))
        live.append((pos if pos is not None else len(at), frame))
    # sorted 是稳定的：链里没有的帧保持它们原来的相对顺序，接在运行区末尾
    return history + [frame for _, frame in sorted(live, key=lambda item: item[0])]


# ═══════════════════════════════════════════════════════════════
# 对账（_save 的唯一调用点）与体检
# ═══════════════════════════════════════════════════════════════

def reconcile(chain: dict, running_frame_ids) -> dict:
    """链与"运行集合"对齐：该挂的挂上、该摘的摘掉。返回 {"append": [...], "drop": [...]}。

    只算**意图**，不动链——挂节点要新节点号（存储层自增），摘节点要删行，
    两件事只有服务层做得到。服务层按 append/drop 调用 attach_tail / detach。

    两个方向的偏差都要修：
    - 运行中但链上没有 → 按数组顺序挂到链尾（老数据首挂，或某条路径漏挂）；
    - 链上有但已不在运行集合 → 摘掉（pop/close/挂起/代销/归并都会造成这种）。
    同一个帧挂了两个节点时留**靠近链尾**的那个（最近的位次才算数），其余丢掉。
    """
    order = [str(fid) for fid in dict.fromkeys(running_frame_ids or ()) if fid]
    alive = set(order)
    keep: dict[str, int] = {}
    drop: list[int] = []
    for node_id in queue(chain):
        frame_id = frame_of_node(chain, node_id)
        if frame_id not in alive or frame_id in keep:
            drop.append(node_id)
        else:
            keep[frame_id] = node_id
    append = [frame_id for frame_id in order if frame_id not in keep]
    return {"append": append, "drop": drop}


def problems(chain: dict) -> list[str]:
    """不变量体检，返回问题清单（空 = 链是好的）。测试用它，线上只在告警里念个数。"""
    nodes = (chain or {}).get("nodes") or {}
    out: list[str] = []
    head, tail = (chain or {}).get("head"), (chain or {}).get("tail")
    if not nodes:
        return ["空链仍挂着 head/tail"] if (head is not None or tail is not None) else []
    if head is None or tail is None:
        out.append(f"非空链缺指针 head={head} tail={tail}")
    if head in nodes and nodes[head].get("prev") is not None:
        out.append(f"头节点 {head} 还有前驱")
    if tail in nodes and nodes[tail].get("next") is not None:
        out.append(f"尾节点 {tail} 还有后继")
    for node_id, node in nodes.items():
        prev, nxt = node.get("prev"), node.get("next")
        if prev is not None and (prev not in nodes or nodes[prev].get("next") != node_id):
            out.append(f"节点 {node_id} 的前驱 {prev} 不回指")
        if nxt is not None and (nxt not in nodes or nodes[nxt].get("prev") != node_id):
            out.append(f"节点 {node_id} 的后继 {nxt} 不回指")
    walked: list[int] = []
    cur, seen = head, set()
    while cur is not None and cur in nodes and cur not in seen:
        walked.append(cur)
        seen.add(cur)
        cur = nodes[cur].get("next")
    if len(walked) != len(nodes):
        out.append(f"从链头只走到 {len(walked)}/{len(nodes)} 个节点（有环或断头）")
    elif walked and nodes[walked[-1]].get("next") is not None:
        out.append("链尾之后还能继续走：链上有环")
    frames = [str(node.get("frame") or "") for node in nodes.values()]
    if len(set(frames)) != len(frames):
        out.append("同一个帧挂了多个节点")
    if len((chain or {}).get("index") or {}) != len(nodes):
        out.append("index 与 nodes 数量不符")
    return out
