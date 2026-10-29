"""
通道对话额度 — 准入、记账、覆盖、重置（唯一入口）

三件事收在这一处，因为它们是同一个账的三面：
  decide()  落库之前问一句"这个对话还能不能用"（插件调用；超限时给一句可发的说明）
  charge()  一轮模型调用之后记账（由 conversation_log_service 统一调用，一处不漏）
  overview()/set_*()  给页面读写这份账

键与口径：
  conversation 组成为 group:<Copro 群 id> / dm:<私信会话 id>；
  口径与额度都可能有"默认（按 AI × 通道种类）"和"单个对话覆盖"两层，
  解析规则只有 _effective() 一处，别在别处再写一遍。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text

from app.utils.pure import channel_token as pure

logger = logging.getLogger(__name__)

_UPSERT_ROW = text("""
    INSERT INTO channel_conversation_usage (
        agent_id, channel_kind, conversation, label, copree_group_id, dm_session_id,
        used_total, used_billable, created_at, updated_at
    ) VALUES (
        :agent_id, :channel_kind, :conversation, :label, :copree_group_id, :dm_session_id,
        0, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
    )
    ON CONFLICT (agent_id, channel_kind, conversation) DO UPDATE SET
        label = CASE WHEN EXCLUDED.label <> '' THEN EXCLUDED.label
                     ELSE channel_conversation_usage.label END,
        copree_group_id = COALESCE(EXCLUDED.copree_group_id, channel_conversation_usage.copree_group_id),
        dm_session_id = COALESCE(EXCLUDED.dm_session_id, channel_conversation_usage.dm_session_id),
        updated_at = CURRENT_TIMESTAMP
    RETURNING *
""")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _effective(policy: dict, row: dict | None) -> tuple[str, int]:
    """(口径, 额度)：单对话覆盖优先，否则跟随该 AI 在该通道上的默认"""
    row = row or {}
    mode = pure.normalize_mode(row.get("meter_mode_override") or policy.get("meter_mode"))
    limit = pure.normalize_limit(
        row.get("limit_override") if row.get("limit_override") is not None else policy.get("limit_tokens")
    )
    return mode, limit


async def get_policy(db, agent_id: int, channel_kind: str) -> dict:
    """读默认策略；没有就现建一份默认的（惰性建行，省掉"先初始化"这一步）"""
    params = {"agent_id": int(agent_id), "channel_kind": str(channel_kind)}
    row = (await db.execute(text(
        "SELECT meter_mode, limit_tokens, blocked_reply_enabled, blocked_reply_text "
        "FROM channel_token_policy WHERE agent_id=:agent_id AND channel_kind=:channel_kind"
    ), params)).mappings().first()
    if row is None:
        await db.execute(text("""
            INSERT INTO channel_token_policy (agent_id, channel_kind, meter_mode, limit_tokens,
                                              blocked_reply_enabled, updated_at)
            VALUES (:agent_id, :channel_kind, :mode, :limit, true, CURRENT_TIMESTAMP)
            ON CONFLICT (agent_id, channel_kind) DO NOTHING
        """), {**params, "mode": pure.DEFAULT_METER_MODE, "limit": pure.DEFAULT_LIMIT_TOKENS})
        row = (await db.execute(text(
            "SELECT meter_mode, limit_tokens, blocked_reply_enabled, blocked_reply_text "
            "FROM channel_token_policy WHERE agent_id=:agent_id AND channel_kind=:channel_kind"
        ), params)).mappings().first()
    data = dict(row or {})
    return {
        "meter_mode": pure.normalize_mode(data.get("meter_mode")),
        "limit_tokens": pure.normalize_limit(data.get("limit_tokens")),
        "blocked_reply_enabled": bool(data.get("blocked_reply_enabled", True)),
        "blocked_reply_text": str(data.get("blocked_reply_text") or ""),
    }


async def _touch_row(db, *, agent_id: int, channel_kind: str, conversation: str, label: str = "",
                     copree_group_id: int | None = None, dm_session_id: str | None = None) -> dict:
    """登记/刷新一个对话的行，并把它读回来。"""
    params = {
        "agent_id": int(agent_id),
        "channel_kind": str(channel_kind),
        "conversation": str(conversation),
        "label": (label or "")[:120],
        "copree_group_id": int(copree_group_id) if copree_group_id else None,
        "dm_session_id": str(dm_session_id) if dm_session_id else None,
    }
    # upsert 直接 RETURNING：准入路径上每条消息都会走一次，别为读回自己的行再跑一趟
    row = (await db.execute(_UPSERT_ROW, params)).mappings().first()
    return dict(row or {})


async def decide(db, *, agent_id: int, channel_kind: str, conversation: str, label: str = "",
                 copree_group_id: int | None = None, dm_session_id: str | None = None) -> dict:
    """落库之前的准入：这个对话还能不能继续用？

    返回里的 notice 非空 = 这一次该回一句说明。它不是每次都非空：
    同一个对话每 BLOCKED_NOTICE_INTERVAL_SECONDS 最多提醒一次，否则被刷屏的群
    会每来一条就回一句"额度用完"，等于机器人在自己刷屏。
    """
    policy = await get_policy(db, agent_id, channel_kind)
    row = await _touch_row(
        db, agent_id=agent_id, channel_kind=channel_kind, conversation=conversation, label=label,
        copree_group_id=copree_group_id, dm_session_id=dm_session_id,
    )
    mode, limit = _effective(policy, row)
    used = pure.used_tokens(mode, row.get("used_total"), row.get("used_billable"))
    blocked = pure.exhausted(limit, used)
    notice = ""
    if blocked and policy["blocked_reply_enabled"]:
        last = row.get("blocked_notice_at")
        due = last is None or (_now() - last) >= timedelta(seconds=pure.BLOCKED_NOTICE_INTERVAL_SECONDS)
        if due:
            notice = pure.blocked_reply_text(policy["blocked_reply_text"])
            await db.execute(text(
                "UPDATE channel_conversation_usage SET blocked_notice_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP "
                "WHERE agent_id=:agent_id AND channel_kind=:channel_kind AND conversation=:conversation"
            ), {"agent_id": int(agent_id), "channel_kind": str(channel_kind), "conversation": str(conversation)})
    return {
        "allowed": not blocked,
        "conversation": conversation,
        "meter_mode": mode,
        "limit_tokens": limit,
        "used_tokens": used,
        "remaining_tokens": pure.remaining(limit, used),
        "notice": notice,
    }


_GROUP_ORIGIN = text(
    "SELECT origin_channel, name FROM groups WHERE id=:group_id"
)
_DM_ORIGIN = text("""
    SELECT u.id, u.username, u.origin_channel, u.type
    FROM dm_sessions s
    JOIN agents a ON a.id = :agent_id
    JOIN users u ON u.id = CASE WHEN s.user1_id <> a.user_id THEN s.user1_id ELSE s.user2_id END
    WHERE s.session_id = :session_id
""")
_CHARGE_ROW = text("""
    INSERT INTO channel_conversation_usage (
        agent_id, channel_kind, conversation, label, copree_group_id, dm_session_id,
        used_total, used_billable, created_at, updated_at
    ) VALUES (
        :agent_id, :channel_kind, :conversation, :label, :copree_group_id, :dm_session_id,
        :total, :billable, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
    )
    ON CONFLICT (agent_id, channel_kind, conversation) DO UPDATE SET
        used_total = channel_conversation_usage.used_total + EXCLUDED.used_total,
        used_billable = channel_conversation_usage.used_billable + EXCLUDED.used_billable,
        label = CASE WHEN EXCLUDED.label <> '' THEN EXCLUDED.label
                     ELSE channel_conversation_usage.label END,
        updated_at = CURRENT_TIMESTAMP
""")


async def charge(db, *, agent_id: int, group_id: int | None = None, session_id: str | None = None,
                 usage: dict | None = None) -> int:
    """某一轮模型调用之后记账；不是通道对话就什么都不做（返回 0）。

    "是不是通道对话"不用另记一张绑定表：落点群自己带着 origin_channel（群是通道建的/认领的
    才会带），私聊则看对端用户——外部通道来的人建号时就带着 origin_channel。
    """
    if int(agent_id or 0) <= 0 or not usage:
        return 0
    total = pure.charged_tokens(pure.METER_TOTAL, usage)
    billable = pure.charged_tokens(pure.METER_BILLABLE, usage)
    if total <= 0 and billable <= 0:
        return 0

    kind = ""
    label = ""
    conversation = ""
    copree_group_id: int | None = None
    dm_session_id: str | None = None

    if group_id:
        row = (await db.execute(_GROUP_ORIGIN, {"group_id": int(group_id)})).mappings().first()
        kind = str((row or {}).get("origin_channel") or "")
        label = str((row or {}).get("name") or "")
        conversation = pure.conversation_key("group", int(group_id))
        copree_group_id = int(group_id)
    elif session_id:
        # 自己是谁的私聊在 SQL 里连出来：每条消息多一次查询只为拿 owner_id，不值得
        row = (await db.execute(_DM_ORIGIN, {
            "session_id": str(session_id), "agent_id": int(agent_id),
        })).mappings().first()
        kind = str((row or {}).get("origin_channel") or "")
        label = str((row or {}).get("username") or "")
        conversation = pure.conversation_key("dm", str(session_id))
        dm_session_id = str(session_id)

    if not kind or not conversation:
        return 0
    await db.execute(_CHARGE_ROW, {
        "agent_id": int(agent_id), "channel_kind": kind, "conversation": conversation,
        "label": label[:120], "copree_group_id": copree_group_id, "dm_session_id": dm_session_id,
        "total": total, "billable": billable,
    })
    return 1


async def overview(db, agent_id: int, channel_kind: str) -> dict:
    """页面要的全部数：默认策略 + 每个对话的口径/额度/已用/剩余/是否已用完"""
    policy = await get_policy(db, agent_id, channel_kind)
    rows = (await db.execute(text(
        "SELECT * FROM channel_conversation_usage WHERE agent_id=:agent_id AND channel_kind=:channel_kind "
        "ORDER BY updated_at DESC NULLS LAST, id DESC LIMIT 200"
    ), {"agent_id": int(agent_id), "channel_kind": str(channel_kind)})).mappings().all()

    conversations = []
    for raw in rows:
        row = dict(raw)
        mode, limit = _effective(policy, row)
        used = pure.used_tokens(mode, row.get("used_total"), row.get("used_billable"))
        conversations.append({
            "conversation": row["conversation"],
            "label": row.get("label") or "",
            "meter_mode": mode,
            "meter_mode_override": row.get("meter_mode_override") or "",
            "limit_tokens": limit,
            "limit_override": row.get("limit_override"),
            "used_tokens": used,
            "remaining_tokens": pure.remaining(limit, used),
            "blocked": pure.exhausted(limit, used),
        })
    # 已用完的排前面：页面上最需要先看到的就是"哪些对话现在不能用"
    conversations.sort(key=lambda c: (not c["blocked"], c["remaining_tokens"]))
    return {"policy": policy, "conversations": conversations,
            "default_blocked_reply": pure.DEFAULT_BLOCKED_REPLY}


async def set_policy(db, agent_id: int, channel_kind: str, *,
                     meter_mode: str | None = None, limit_tokens: int | None = None,
                     blocked_reply_enabled: bool | None = None,
                     blocked_reply_text: str | None = None) -> dict:
    """改默认策略。只改传进来的字段（None = 不动），避免"保存一次把别的项清空"。"""
    await get_policy(db, agent_id, channel_kind)
    sets: list[str] = []
    params: dict[str, Any] = {"agent_id": int(agent_id), "channel_kind": str(channel_kind)}
    if meter_mode is not None:
        sets.append("meter_mode=:meter_mode")
        params["meter_mode"] = pure.normalize_mode(meter_mode)
    if limit_tokens is not None:
        sets.append("limit_tokens=:limit_tokens")
        params["limit_tokens"] = pure.normalize_limit(limit_tokens)
    if blocked_reply_enabled is not None:
        sets.append("blocked_reply_enabled=:blocked_reply_enabled")
        params["blocked_reply_enabled"] = bool(blocked_reply_enabled)
    if blocked_reply_text is not None:
        sets.append("blocked_reply_text=:blocked_reply_text")
        params["blocked_reply_text"] = str(blocked_reply_text).strip() or None
    if sets:
        await db.execute(text(
            f"UPDATE channel_token_policy SET {', '.join(sets)}, updated_at=CURRENT_TIMESTAMP "
            "WHERE agent_id=:agent_id AND channel_kind=:channel_kind"
        ), params)
    return await get_policy(db, agent_id, channel_kind)


async def set_override(db, agent_id: int, channel_kind: str, conversation: str, *,
                       limit_tokens: int | None = None, meter_mode: str | None = None,
                       clear: bool = False) -> None:
    """给单个对话设覆盖；clear=True 或两项都给 None = 回到跟随默认"""
    params = {"agent_id": int(agent_id), "channel_kind": str(channel_kind), "conversation": str(conversation)}
    if clear or (limit_tokens is None and meter_mode is None):
        await db.execute(text(
            "UPDATE channel_conversation_usage SET limit_override=NULL, meter_mode_override=NULL, "
            "updated_at=CURRENT_TIMESTAMP WHERE agent_id=:agent_id AND channel_kind=:channel_kind AND conversation=:conversation"
        ), params)
        return
    sets: list[str] = []
    if limit_tokens is not None:
        sets.append("limit_override=:limit_override")
        params["limit_override"] = pure.normalize_limit(limit_tokens)
    if meter_mode is not None:
        sets.append("meter_mode_override=:mode_override")
        params["mode_override"] = pure.normalize_mode(meter_mode)
    sets.append("updated_at=CURRENT_TIMESTAMP")
    await db.execute(text(
        f"UPDATE channel_conversation_usage SET {', '.join(sets)} "
        "WHERE agent_id=:agent_id AND channel_kind=:channel_kind AND conversation=:conversation"
    ), params)


async def reset_usage(db, agent_id: int, channel_kind: str, conversation: str | None = None) -> None:
    """把已用量清零（也清掉"提醒过了"的节流时间：额度回来了，下次超限该重新提醒）。

    只清某一个对话，或整个通道——重置是"把钱还给它"，不是"删掉这条对话"。
    """
    params = {"agent_id": int(agent_id), "channel_kind": str(channel_kind)}
    where = "agent_id=:agent_id AND channel_kind=:channel_kind"
    if conversation:
        where += " AND conversation=:conversation"
        params["conversation"] = str(conversation)
    await db.execute(text(
        "UPDATE channel_conversation_usage SET used_total=0, used_billable=0, "
        f"blocked_notice_at=NULL, updated_at=CURRENT_TIMESTAMP WHERE {where}"
    ), params)
