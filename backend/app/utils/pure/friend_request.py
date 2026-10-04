"""待处理好友申请在账本里的投递与作废 —— 文案与幂等键的唯一出处。

为什么落账本、而不是每轮在尾部念一遍：申请可能挂好几天，而尾部块每轮重拼 = 每轮都付一次钱；
它是"有人来敲过门"这一件事，不是一份读数。落进账本写一次，之后每轮命中。
为什么作废是**再补一条**而不是改掉原来那条：账本段内只追加，改中段就断前缀缓存
（见 history.py §0），撤回/处理掉的语义由后来这条作废通知承担。
"""
from __future__ import annotations

from app.utils.pure.history import make_entry

_PREFIX = "friend_request:"


def request_ref(request_id) -> str:
    """一条申请的幂等键：一个申请一条，AI 看过就算投过。"""
    return f"{_PREFIX}{int(request_id)}"


def delivered_request_ids(entries: list[dict]) -> set[int]:
    """账本里已经投递过的申请 id —— 投递幂等的唯一依据（账本自己就是游标，不另立一份状态）"""
    out: set[int] = set()
    for e in entries or []:
        ref = str((e or {}).get("ref") or "")
        tail = ref[len(_PREFIX):] if ref.startswith(_PREFIX) else ""
        if tail.isdigit():
            out.add(int(tail))
    return out


def delivery_entry(req: dict) -> dict:
    """一条申请投进账本长什么样。申请 id 必须写进去：处理它要拿它当参数。"""
    msg = str(req.get("message") or "").strip()
    who = str(req.get("name") or "").strip() or "有人"
    return make_entry(
        "notice",
        f"【好友申请】「{who}」申请加你为好友" + (f"，留言：{msg[:120]}" if msg else "（没留言）") + "。\n"
        f"要处理就调 handle_friend_request（申请 id={req.get('id')}，通过或拒绝都行）；"
        "不处理也可以，申请会一直挂着。",
        actor="system", ref=request_ref(req.get("id")), flags={"drop_on_unlock": True},
    )


def retired_entry(request_id) -> dict:
    """申请不在待处理里了（你处理掉了、或对方撤回）→ 补一条作废，别让它继续以为门口有人"""
    return make_entry(
        "notice",
        f"【好友申请】id={int(request_id)} 那条已经不在了：你处理掉了，或者对方撤回了。不用再管它。",
        actor="system", ref=request_ref(request_id), flags={"drop_on_unlock": True},
    )
