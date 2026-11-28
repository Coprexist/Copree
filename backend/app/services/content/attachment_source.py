"""会话里「最近一条带附件的消息」—— file_fetch 的取数层

为什么单独一层：**哪条消息算数、attachments 怎么解析**（群聊是 JSONB、私信是 Text 存的
JSON 字符串）是领域事实；工具只该决定「收哪几个、怎么回执」。两件事混在工具类里时，
换个取数口径（比如以后按 file_id 反查所在消息）就得去动工具，而工具是被模型当作契约看的。

编号口径来自 multimodal.attachment_sequence：上下文里给 AI 看的 [图片 2] 与
file_fetch(indexes=[2]) 必须是同一个东西，所以两侧都只认这一份函数。
"""
from __future__ import annotations

import json
from typing import Iterable, NamedTuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.utils.multimodal import attachment_sequence


async def latest_message_attachments(
    db: AsyncSession, *, group_id: int | None = None, session_id: str | None = None,
) -> list[dict]:
    """当前会话最近一条带附件消息里的附件（原始快照列表；没有则空列表）

    私信按 session_id、群聊按 group_id：两者只会给一个（工具按上下文决定）。
    """
    if session_id:
        sql, params = (
            "SELECT attachments FROM dm_messages WHERE session_id = :sid "
            "AND attachments IS NOT NULL ORDER BY id DESC LIMIT 1",
            {"sid": session_id},
        )
    elif group_id:
        sql, params = (
            "SELECT attachments FROM messages WHERE group_id = :gid "
            "AND attachments IS NOT NULL ORDER BY id DESC LIMIT 1",
            {"gid": group_id},
        )
    else:
        return []

    row = (await db.execute(text(sql), params)).first()
    if not row or not row[0]:
        return []
    payload = row[0]
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            return []
    return list(payload) if isinstance(payload, list) else []


class FetchSelection(NamedTuple):
    """一次收取的选区：要收哪些、哪些编号不存在、这条消息可选哪些编号"""

    file_ids: tuple[int, ...] = ()
    missing_indexes: tuple[int, ...] = ()
    available_indexes: tuple[int, ...] = ()

    @property
    def is_empty(self) -> bool:
        """选完一个也不剩，且不是「编号不存在」那种错误"""
        return not self.file_ids and not self.missing_indexes


def select_for_fetch(
    attachments: Iterable[dict],
    *,
    file_ids: Iterable[int] | None = None,
    indexes: Iterable[int] | None = None,
) -> FetchSelection:
    """把两种指认（按 id / 按消息里的编号）收敛成一份选区

    - 给 file_ids：按 id 收，不校验它们属于这条消息（附件可能是历史里的）；
    - 给 indexes：按编号收，编号取自 attachment_sequence，缺号如实报出来；
    - 都不给：收这条消息里**全部**可收附件（旧实现只取第一个，是漏收不是约定）。
    """
    seq = attachment_sequence(list(attachments))
    available = tuple(n for n, _ in seq)

    wanted_ids = _coerce_ids(file_ids)
    if wanted_ids:
        return FetchSelection(file_ids=wanted_ids, available_indexes=available)

    wanted_indexes = _coerce_ids(indexes)
    if not wanted_indexes:
        return FetchSelection(
            file_ids=tuple(int(att["file_id"]) for _, att in seq if att.get("file_id")),
            available_indexes=available,
        )

    by_index = {n: att for n, att in seq}
    picked: list[int] = []
    missing: list[int] = []
    for i in wanted_indexes:
        att = by_index.get(i)
        fid = att.get("file_id") if att else None
        if fid is None:
            missing.append(i)
        else:
            picked.append(int(fid))
    return FetchSelection(
        file_ids=tuple(picked), missing_indexes=tuple(missing), available_indexes=available,
    )


def _coerce_ids(value) -> tuple[int, ...]:
    """入参统一成 int 元组：宽容单值（模型偶尔把 [1] 写成 1），非法值直接丢"""
    if value is None:
        return ()
    if isinstance(value, (int, str)) and not isinstance(value, bool):
        value = [value]
    if not isinstance(value, (list, tuple, set)):
        return ()
    out: list[int] = []
    for v in value:
        try:
            out.append(int(v))
        except (TypeError, ValueError):
            continue
    return tuple(out)
