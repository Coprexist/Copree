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
    """一行复述归属，供状态摘要每轮念给 AI 听。"""
    parts = []
    if names := [f["name"] for f in of_session(foci, context_ref)]:
        parts.append("会话焦段：" + "、".join(names))
    if semantic_focus_id and (focus := find(foci, semantic_focus_id)) and focus.get("axis") == SEMANTIC:
        parts.append(f"语义焦段：{focus['name']}")
    return " · ".join(parts)


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
