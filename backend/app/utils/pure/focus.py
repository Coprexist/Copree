"""
焦段纯函数 — 无 IO、无 DB 依赖。

两条轴上的「焦段」是记忆的适用范围：
- 会话焦段（session）：一组同类对话，元素是 context_ref；
- 语义焦段（semantic）：一段话题，记忆直接锚在它身上。

平台预置一个会话焦段「所有聊天」，判定恒命中——"全局"要显式锚它，没有隐式全局。
其余焦段由 AI 自建，可改名、可合并；焦段不宜多、不宜相似。
"""
from __future__ import annotations

import uuid

SESSION = "session"
SEMANTIC = "semantic"
AXES = (SESSION, SEMANTIC)

ALL_CHATS_ID = "all-chats"
ALL_CHATS_NAME = "所有聊天"

MAX_FOCI = 20          # 超过就提示合并，别让焦段长成一片杂草
MAX_NAME_CHARS = 20


def all_chats() -> dict:
    """预置的会话焦段：任何会话都属于它。"""
    return {"id": ALL_CHATS_ID, "axis": SESSION, "name": ALL_CHATS_NAME,
            "elements": [], "builtin": True}


def _clean_name(name: str) -> str:
    return (name or "").strip()[:MAX_NAME_CHARS]


def normalize(foci) -> list[dict]:
    """读入即清洗：结构合法化，并保证预置焦段始终在列（旧数据还没有 foci 也一样）。"""
    out: list[dict] = []
    seen: set[str] = set()
    for item in (foci or []):
        if not isinstance(item, dict):
            continue
        fid = str(item.get("id") or "").strip()
        name = _clean_name(item.get("name"))
        axis = item.get("axis")
        if not fid or not name or axis not in AXES or fid in seen:
            continue
        if fid == ALL_CHATS_ID:
            continue                      # 预置焦段以 all_chats() 为准，不看存量
        seen.add(fid)
        out.append({
            "id": fid,
            "axis": axis,
            "name": name,
            "elements": [str(e).strip() for e in (item.get("elements") or []) if str(e).strip()],
            "builtin": False,
        })
    out.insert(0, all_chats())
    return out


def storable(foci) -> list[dict]:
    """落库前剔掉预置焦段——它由纯函数兜底注入，不该占存量数据。"""
    return [f for f in normalize(foci) if not f.get("builtin")]


def find(foci, focus_id: str) -> dict | None:
    fid = str(focus_id or "").strip()
    return next((f for f in (foci or []) if f.get("id") == fid), None)


def by_name(foci, name: str) -> dict | None:
    target = _clean_name(name)
    return next((f for f in (foci or []) if f.get("name") == target), None)


def add(foci, name: str, axis: str) -> tuple[list[dict], dict | None, str]:
    """新建焦段；同名则返回既有那个，不重复建。"""
    foci = normalize(foci)
    name = _clean_name(name)
    if not name:
        return foci, None, "焦段名不能为空"
    if axis not in AXES:
        return foci, None, f"未知的轴：{axis}"
    if existing := by_name(foci, name):
        return foci, existing, f"已经有「{name}」了，直接用它"
    if len(foci) >= MAX_FOCI:
        return foci, None, f"焦段已到上限 {MAX_FOCI}，先合并或删掉不用的"
    focus = {"id": uuid.uuid4().hex[:8], "axis": axis, "name": name,
             "elements": [], "builtin": False}
    foci.append(focus)
    return foci, focus, f"已新建{'会话' if axis == SESSION else '语义'}焦段「{name}」"


def rename(foci, focus_id: str, new_name: str) -> tuple[list[dict], str]:
    foci = normalize(foci)
    focus = find(foci, focus_id)
    if focus is None:
        return foci, f"没找到焦段 {focus_id}"
    if focus.get("builtin"):
        return foci, f"「{ALL_CHATS_NAME}」是预置焦段，不能改名"
    new_name = _clean_name(new_name)
    if not new_name:
        return foci, "新名字不能为空"
    if (other := by_name(foci, new_name)) and other.get("id") != focus_id:
        return foci, f"已经有叫「{new_name}」的焦段了，要合请用合并"
    old = focus["name"]
    focus["name"] = new_name
    return foci, f"焦段「{old}」已改名为「{new_name}」"


def merge(foci, keep_id: str, drop_id: str) -> tuple[list[dict], str]:
    """合并：drop 的名字与元素并进 keep，随后删掉 drop。"""
    foci = normalize(foci)
    keep, drop = find(foci, keep_id), find(foci, drop_id)
    if keep is None or drop is None:
        return foci, "keep_id / drop_id 至少有一个不存在"
    if keep["id"] == drop["id"]:
        return foci, "同一个焦段不用合并"
    if drop.get("builtin"):
        return foci, f"「{ALL_CHATS_NAME}」是预置焦段，不能被合并掉"
    keep["elements"] = list(dict.fromkeys(
        (keep.get("elements") or []) + (drop.get("elements") or [])))
    foci = [f for f in foci if f["id"] != drop["id"]]
    return foci, f"「{drop['name']}」已并入「{keep['name']}」"


def join(foci, focus_id: str, context_ref: str) -> tuple[list[dict], str]:
    """把当前会话挂进某个会话焦段——显式动作，不按对话类型自动归档。"""
    foci = normalize(foci)
    focus = find(foci, focus_id)
    if focus is None:
        return foci, f"没找到焦段 {focus_id}"
    if focus.get("axis") != SESSION:
        return foci, f"「{focus['name']}」是语义焦段，挂不了会话"
    if focus.get("builtin"):
        return foci, f"「{ALL_CHATS_NAME}」是预置焦段，任何会话本来就在里面"
    ref = str(context_ref or "").strip()
    if not ref:
        return foci, "拿不到当前会话标识，挂不上"
    if ref not in (focus.get("elements") or []):
        focus.setdefault("elements", []).append(ref)
    return foci, f"已把当前会话归入「{focus['name']}」"


def leave(foci, focus_id: str, context_ref: str) -> tuple[list[dict], str]:
    foci = normalize(foci)
    focus = find(foci, focus_id)
    if focus is None:
        return foci, f"没找到焦段 {focus_id}"
    ref = str(context_ref or "").strip()
    if ref in (focus.get("elements") or []):
        focus["elements"] = [e for e in focus["elements"] if e != ref]
        return foci, f"已把当前会话移出「{focus['name']}」"
    return foci, f"当前会话本来就不在「{focus['name']}」里"


def of_session(foci, context_ref: str) -> list[dict]:
    """当前会话属于哪些会话焦段（预置的那个恒命中）。"""
    ref = str(context_ref or "").strip()
    return [f for f in normalize(foci)
            if f.get("axis") == SESSION
            and (f.get("builtin") or ref in (f.get("elements") or []))]


def describe(foci, context_ref: str, semantic_focus_id: str = "") -> str:
    """一行复述归属，供状态摘要每轮念给 AI 听。

    只说"你在这儿属于哪些焦段"。**空焦段不在这里念**：那是"里面的人全走了"这条一次性事实，
    每轮重拼的尾部动态块永远吃不到缓存，它该走账本条目（empty_pending / format_empty_notice）。
    """
    parts = []
    if names := [f["name"] for f in of_session(foci, context_ref)]:
        parts.append("会话焦段：" + "、".join(names))
    if semantic_focus_id and (focus := find(foci, semantic_focus_id)) and focus.get("axis") == SEMANTIC:
        parts.append(f"语义焦段：{focus['name']}")
    return " · ".join(parts)


def empty_sessions(foci) -> list[dict]:
    """一个会话都没有的会话焦段（预置的「所有聊天」不算，它本来就没人）。"""
    return [f for f in normalize(foci)
            if f.get("axis") == SESSION and not f.get("builtin") and not (f.get("elements") or [])]


def empty_notice_key(focus_id: str) -> str:
    """「这个空焦段已经告诉过他了」的投递键——存在会话帧的 delivered 里。

    为什么借触发规则那份投递进度：解锁（compact/clear）会把它一起归零，
    于是"条件还在就说一遍"自然成立，不必再为焦段另建一处会跟解锁走散的状态。
    """
    return f"emptyfocus:{focus_id}"


def empty_pending(foci, delivered) -> list[dict]:
    """空掉、且本会话还没告诉过他的会话焦段（投递的唯一依据）。

    判据是**当下**的 elements，所以焦段重新有人之后这里不会再命中——
    "空"不是一次事件，它只是一个当下成立的事实，说过了就该闭嘴，除非解锁后重新想起。
    """
    sent = delivered or {}
    return [f for f in empty_sessions(foci) if empty_notice_key(f["id"]) not in sent]


def format_empty_notice(foci: list[dict]) -> str:
    """空焦段告知：锚在它上面的记忆在哪儿都召不回（§九），而 AI 自己看不见这件事。

    只说事实与出路，不替它搬记忆：唯一能自动做的动作（改锚「所有聊天」）等于把群里的
    事搬进所有会话，那条隐私边界不该由一次后台动作决定。
    """
    lines = [f"【空焦段 · {len(foci)} 个】",
             "这些会话焦段里的会话已经全部消失了，锚在它们身上的记忆在哪儿都召不回："]
    for f in foci:
        lines.append(f"- {f['name']}（{f['id']}）")
    lines.append("出路：用 merge_focus 并进一个还有人的焦段（或并入「所有聊天」），"
                 "或把那些记忆改锚到别的焦段——别让记忆挂在一个够不着的地方。")
    return "\n".join(lines)


def brief(foci, context_ref: str = "", semantic_focus_id: str = "") -> list[dict]:
    """list_focus 的返回体：焦段清单 + 当前是否命中。"""
    active = {f["id"] for f in of_session(foci, context_ref)} | {semantic_focus_id}
    return [{
        "id": f["id"],
        "name": f["name"],
        "axis": f["axis"],
        "elements": len(f.get("elements") or []),
        "builtin": bool(f.get("builtin")),
        "current": f["id"] in active,
    } for f in normalize(foci)]


def covers_session(foci, focus_id: str, context_ref: str) -> bool:
    """某个会话焦段覆不覆盖这个会话（预置「所有聊天」恒命中）。

    与记忆可达性、变更通知作用域共用一份判定——两处各写一份就会出现"通知按一个口径、
    召回按另一个口径"的分裂。
    """
    if str(focus_id or "") == ALL_CHATS_ID:
        return True                      # 预置焦段不落库、由纯函数兜底：任何会话场景恒命中
    focus = find(foci, focus_id)
    if not focus or focus.get("axis") != SESSION:
        return False
    return str(context_ref or "") in (focus.get("elements") or [])


def memory_reach(session_refs, session_foci, semantic_foci, *,
                 context_ref: str = "", semantic_focus: str = "", foci=None) -> int:
    """这条记忆在当前上下文里够不够得着：返回命中的元素数，0 = 够不着。

    - 任一元素命中即召回（或）；命中多的排在前面，所以计数而不是布尔；
    - 不要求两轴同时命中：群里讲过的化学，在私信里也该想得起来；
    - 三组全空 = 空集语义（第六节）：只有当前会话 + 当前语义焦段。写入侧会把这一刻的
      会话与语义焦段物化下来（见 focus_service.default_anchors），所以空集只会出现在
      本机制之前的存量行上——那些行按"在本上下文里够得着"处理，不做静默丢弃。

    为什么要有这个函数：锚点此前只写不读，"换了地方就找不到"只是工具里的一句警告，
    实际照样处处可见，等于隐式全局——与第六节明确相悖。
    """
    refs = normalize_anchor_ids(session_refs)
    s_foci = normalize_anchor_ids(session_foci)
    m_foci = normalize_anchor_ids(semantic_foci)
    if not refs and not s_foci and not m_foci:
        return 1
    ref = str(context_ref or "")
    hit = 1 if (ref and ref in refs) else 0
    hit += sum(1 for fid in s_foci if covers_session(foci, fid, ref))
    if semantic_focus:
        hit += sum(1 for fid in m_foci if fid == semantic_focus)
    return hit


def reachable(reach: int) -> bool:
    """够得着 = 至少命中一个元素（或空集语义的当前上下文）。"""
    return reach > 0


def normalize_anchor_ids(ids) -> list[str]:
    """清洗焦段锚点 id：去空白、去重，保持给出的顺序。"""
    out: list[str] = []
    for item in (ids or []):
        fid = str(item or "").strip()
        if fid and fid not in out:
            out.append(fid)
    return out


def anchor_warning(session_foci, semantic_foci) -> str:
    """一条记忆没锚任何焦段时的提醒。

    空集不是"全局"——它是最窄的一档：只在本会话与当前语义焦段下可见。
    要跨会话共享得显式锚（例如预置的「所有聊天」），所以这里必须说清。
    """
    if normalize_anchor_ids(session_foci) or normalize_anchor_ids(semantic_foci):
        return ""
    return ("未锚定焦段：这条记忆只在本会话与当前语义焦段下能被想起，换个地方就找不到了。"
            "要跨会话共享，请锚定焦段（例如「所有聊天」）。")
