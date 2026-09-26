"""世界事件 → AI（平台侧唯一入口）

世界（群视界）决定"发生什么"和"发给谁"（按 id 或按类型选群/AI）；平台按收件人过决策技能，
没被规则处理掉的一律唤醒本体——叫醒，让 AI 能改自己的规则，也保证有回复。
契约与语义见 docs/group_world/design/world_ai_events.md。
"""
from __future__ import annotations

import logging
import re
import time

from sqlalchemy import select

logger = logging.getLogger(__name__)

MAX_NAME_CHARS = 40
MAX_TITLE_CHARS = 60
MAX_PAYLOAD_BYTES = 4096
MAX_EVENTS_PER_MINUTE = 60          # 每世界每分钟上限（worlds.config.event_per_minute 可覆盖）
_NAME_RE = re.compile(r"^[a-z0-9_]+$")

_rate: dict[int, list[float]] = {}  # world_id → 最近一分钟的投递时刻（滑动窗口）


def validate_event(event: dict) -> tuple[bool, str]:
    """校验事件契约（见文档 §2）：字段缺失/超限一律拒绝，不猜"""
    if not isinstance(event, dict):
        return False, "事件必须是对象"
    name = str(event.get("name") or "").strip()
    if not name or len(name) > MAX_NAME_CHARS or not _NAME_RE.match(name):
        return False, f"name 必填、≤{MAX_NAME_CHARS} 字符、只能小写字母数字下划线（如 restock_done）"
    title = str(event.get("title") or "").strip()
    if not title or len(title) > MAX_TITLE_CHARS:
        return False, f"title 必填且 ≤{MAX_TITLE_CHARS} 字符"
    payload = event.get("payload")
    if payload is not None and not isinstance(payload, dict):
        return False, "payload 必须是对象"
    if payload is not None:
        import json
        if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > MAX_PAYLOAD_BYTES:
            return False, f"payload 超过 {MAX_PAYLOAD_BYTES} 字节"
    if event.get("targets") is not None and not isinstance(event["targets"], dict):
        return False, "targets 必须是对象"
    return True, ""


def _allow_rate(world) -> bool:
    try:
        limit = int((world.config or {}).get("event_per_minute") or MAX_EVENTS_PER_MINUTE)
    except (TypeError, ValueError):
        limit = MAX_EVENTS_PER_MINUTE
    now = time.monotonic()
    stamps = [t for t in _rate.get(world.id, []) if now - t < 60]
    if len(stamps) >= max(1, limit):
        _rate[world.id] = stamps
        return False
    stamps.append(now)
    _rate[world.id] = stamps
    return True


async def resolve_targets(db, world, targets: dict | None) -> list[dict]:
    """把世界给的收件人描述解析成 [(agent_id, group_id)]。

    targets 形状（各字段都可选、可组合）：
      {"group_id": 5, "groups": {"ids": [5], "types": ["study"]}, "ais": {"ids": [24], "types": ["knight"]}}
    group_id 是兜底回复群：没指明群的收件人（直接点名的 AI）用它回话。
    """
    from app.models.agent import Agent
    from app.models.group import GroupMember
    from app.models.world import WorldAgent, WorldBinding

    targets = targets or {}
    groups = targets.get("groups") or {}
    ais = targets.get("ais") or {}
    default_group = int(targets["group_id"]) if targets.get("group_id") else None

    group_ids = {int(i) for i in (groups.get("ids") or [])}
    group_types = {str(t) for t in (groups.get("types") or [])}
    ai_ids = {int(i) for i in (ais.get("ids") or [])}
    ai_types = {str(t) for t in (ais.get("types") or [])}

    if not any([group_ids, group_types, ai_ids, ai_types, default_group]):
        # 世界一个收件人都没指定：默认发本世界绑定的所有群里的 AI
        group_ids |= {int(i) for i in (await db.execute(select(WorldBinding.entity_id).where(
            WorldBinding.world_id == world.id,
            WorldBinding.entity_type == "group",
        ))).scalars().all()}
    if group_types:
        rows = (await db.execute(select(WorldBinding.entity_id).where(
            WorldBinding.world_id == world.id,
            WorldBinding.entity_type == "group",
            WorldBinding.group_type_slug.in_(list(group_types)),
        ))).scalars().all()
        group_ids |= {int(i) for i in rows}
    if default_group and not group_ids and not ai_ids and not ai_types:
        # 只给了群号（没别的要求）：理解为"发给这个群的 AI"；否则群号只是兜底回复群
        group_ids.add(default_group)

    out: list[dict] = []
    seen: set[tuple[int, int | None]] = set()

    def _add(agent_id: int, group_id: int | None) -> None:
        key = (int(agent_id), int(group_id) if group_id else None)
        if key in seen:
            return
        seen.add(key)
        out.append({"agent_id": key[0], "group_id": key[1]})

    for gid in sorted(group_ids):
        members = (await db.execute(select(GroupMember.member_id).where(
            GroupMember.group_id == gid, GroupMember.member_type == "ai",
        ))).scalars().all()
        if not members:
            continue
        rows = (await db.execute(select(Agent.id).where(Agent.user_id.in_(list(members))))).scalars().all()
        for aid in rows:
            _add(aid, gid)

    if not group_ids and not ai_ids and not ai_types:
        # 一个群都没绑：退回世界居民 AI，至少别把事件静默丢掉
        for aid in (await db.execute(select(WorldAgent.agent_id).where(
                WorldAgent.world_id == world.id))).scalars().all():
            _add(aid, default_group)
    if ai_types:
        rows = (await db.execute(select(WorldAgent.agent_id, WorldAgent.group_id).where(
            WorldAgent.world_id == world.id,
            WorldAgent.group_type_slug.in_(list(ai_types)),
        ))).all()
        for aid, gid in rows:
            _add(aid, gid or default_group)

    for aid in (ais.get("ids") or []):
        _add(int(aid), default_group)

    return out


def _enqueue_wake(target: dict, world, event: dict, note: str = "") -> None:
    """未命中规则 → 投唤醒队列（叫醒由 AI 回复 worker 处理，与闹钟/好友申请同一条路）"""
    from app.ai.response_worker import message_queue
    try:
        message_queue.put_nowait({
            "type": "world_event",
            "agent_id": target["agent_id"],
            "group_id": target["group_id"],
            "world_id": world.id,
            "name": event["name"],
            "title": event["title"],
            "payload": event.get("payload") or {},
            "note": note,
        })
    except Exception as e:  # noqa: BLE001 —— 队列满/未启动都不该让世界侧失败
        logger.warning(f"🌐 世界 #{world.id} 事件唤醒入队失败: {e}")


async def emit_event(db, world, event: dict) -> dict:
    """世界发来一条事件：校验 → 解析收件人 → 过决策技能 → 没处理掉的叫醒本体。

    返回 {ok, name, targets, handled, woke}；校验/限频失败返回 {ok: False, error}。
    不提交事务——由调用方（世界工具 / 受控 API）决定提交时机。
    """
    ok, err = validate_event(event)
    if not ok:
        return {"ok": False, "error": err}
    if not _allow_rate(world):
        return {"ok": False, "error": "事件频率超限（每世界每分钟上限）"}

    targets = await resolve_targets(db, world, event.get("targets"))
    if not targets:
        return {"ok": True, "name": event["name"], "targets": 0, "handled": 0, "woke": 0}

    from app.models.agent import Agent
    from app.services.world.decision_skill import (
        build_world_event_ctx, load_rules_map, run_decision_engine, send_group_reply,
    )

    ctx = build_world_event_ctx(world.id, event)
    rules = await load_rules_map(db, "agent", [t["agent_id"] for t in targets])
    handled = woke = 0
    for target in targets:
        dec = await run_decision_engine(db, "agent", target["agent_id"], world, "world_event", ctx,
                                        rules=rules.get(target["agent_id"]))
        if dec.get("hit") and dec.get("handled"):
            handled += 1
            if dec.get("reply") and target["group_id"]:
                user_id = (await db.execute(select(Agent.user_id).where(
                    Agent.id == target["agent_id"]))).scalar_one_or_none()
                if user_id:
                    try:
                        await send_group_reply(db, target["group_id"], user_id, dec["reply"])
                    except Exception as e:  # noqa: BLE001
                        logger.warning(f"🌐 世界 #{world.id} 事件回复代发失败: {e}")
            continue
        _enqueue_wake(target, world, event, note=dec.get("note") or "")
        woke += 1

    logger.info(f"🌐 世界 #{world.id} 事件「{event['name']}」：{len(targets)} 个收件人，"
                f"程序处理 {handled}，唤醒 {woke}")
    return {"ok": True, "name": event["name"], "targets": len(targets), "handled": handled, "woke": woke}
