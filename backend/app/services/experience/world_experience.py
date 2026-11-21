"""世界 AI 的跨情景经历 —— 世界侧没有账本，投递标记记在会话元信息里

主站的「投过没有再投」靠账本条目的 `exp:<id>`；世界侧每轮都按 DB 重建 messages、
没有账本，所以把已投的 id 记在被投会话自己的 `world.config.sessions[key].experience_seen`
里。语义相同：进过这个会话就不再投，换会话（新场景）重新可从「最近别处干过什么」补。

写入与主站同一张表（owner_type=world_ai）：世界 AI 没有 end_turn 的 key_note，
平台兜底就是**这一轮的工具总账**；AI 也可以通过 note_experience 世界工具自己写。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

SEEN_KEY = "experience_seen"
SEEN_MAX = 200          # 一个会话最多记这么多投过的 id，超了丢最旧的
RECORD_TOOLS_MAX = 8    # 兜底一行的工具条数上限（多轮几千字的账塞不进一行）

OWNER_TYPE = "world_ai"


def scene_of(world) -> tuple[str, str]:
    """世界这一场的键与给人看的名字：一个会话一场（默认会话 = 'default'）。"""
    from app.services.world.world_chat_service import session_key

    skey = session_key(world)
    title = str(((world.config or {}).get("sessions") or {}).get(skey, {}).get("title") or "").strip()
    label = f"{world.name}" + (f" · {title}" if title else "")
    return f"world:{world.id}:{skey}", label[:100]


def _seen_ids(world) -> list[str]:
    from app.services.world.world_chat_service import session_key

    meta = ((world.config or {}).get("sessions") or {}).get(session_key(world)) or {}
    return [str(x) for x in (meta.get(SEEN_KEY) or [])]


def _mark_seen(world, ids: list[int]) -> None:
    """把刚投出去的 id 记进当前会话元信息（同一处写、同一处读，不另建表）。"""
    from app.services.world.world_chat_service import session_key

    cfg = dict(world.config or {})
    sessions = dict(cfg.get("sessions") or {})
    skey = session_key(world)
    meta = dict(sessions.get(skey) or {})
    merged = [str(x) for x in (meta.get(SEEN_KEY) or [])]
    for i in ids:
        s = str(i)
        if s not in merged:
            merged.append(s)
    meta[SEEN_KEY] = merged[-SEEN_MAX:]
    sessions[skey] = meta
    cfg["sessions"] = sessions
    world.config = cfg


async def record_world_turn(world_repo, world, wai, *, tools_done: list[str]) -> None:
    """世界侧的轮末兜底：这一轮用过工具就记一行「干了什么」。

    同一场里摘要完全相同的连续轮次不重复写（AI 反复调同一个工具时不该堆出十行）。
    写失败只记日志——经历是「以后能想起」，不该拖垮这一轮对话。
    """
    from app.services.experience import experience_service

    tools = "；".join(str(t).strip() for t in (tools_done or [])[-RECORD_TOOLS_MAX:] if str(t).strip())
    if not tools:
        return
    try:
        scene, label = scene_of(world)
        last = await experience_service.recent(
            world_repo.session, OWNER_TYPE, wai.id, 1)
        if last and last[0].get("scene_key") == scene and last[0].get("summary") == tools:
            return
        await experience_service.record(
            world_repo.session, owner_type=OWNER_TYPE, owner_id=wai.id,
            context_ref=scene, summary=tools, label=label,
            source=experience_service.SOURCE_TOOL_LEDGER, frame_type="world",
        )
    except Exception as e:
        logger.warning(f"🌐 世界 #{getattr(world, 'id', '?')} 经历写入失败（非致命）: {e}")


async def deliver_world_experiences(
    world_repo, world, wai, messages: list[dict], *,
    query: str, limit: int, fresh: bool,
) -> int:
    """把别的会话里干过的事补进这一轮的尾部（返回投出去的条数）。

    新会话（fresh）先补「最近离开的那一场」，再按语境召回；两个来源共用一个 seen 列表，
    所以同一行在这个会话里只会出现一次。找不到相关的就不投，不拿时间序凑数。
    """
    from app.services.experience import experience_service
    from app.utils.pure.experience_entry import belongs_elsewhere

    if limit <= 0:
        return 0
    try:
        scene, _label = scene_of(world)
        seen = set(_seen_ids(world))
        rows: list[dict] = []

        if fresh:
            left = await experience_service.latest_other_scene(
                world_repo.session, OWNER_TYPE, wai.id, scene)
            if left:
                rows += await experience_service.scene_rows(
                    world_repo.session, OWNER_TYPE, wai.id, left, limit)

        if query.strip():
            rows += await experience_service.search(
                world_repo.session, OWNER_TYPE, wai.id, scene, query, limit)

        picked: list[dict] = []
        for row in rows:
            if len(picked) >= limit:
                break
            if str(row.get("id")) in seen:
                continue
            if not belongs_elsewhere(row, scene):
                continue
            if any(p["id"] == row["id"] for p in picked):
                continue
            picked.append(row)

        if not picked:
            return 0
        lines = [
            f"- {r.get('when_label') or ''} {r.get('scene_label') or ''}".strip()
            + f"：{r.get('summary') or ''}"
            for r in reversed(picked)
        ]
        messages.append({"role": "system", "content": (
            "## 你在别的会话里做过的事\n" + "\n".join(lines) +
            "\n（这些是你自己在别的会话里做的，不是当前会话发生的；"
            "别把它们当成这里说好的事，也不要在这里复述其中的私事。）"
        )})
        _mark_seen(world, [r["id"] for r in picked])
        await world_repo.commit()
        return len(picked)
    except Exception as e:
        logger.warning(f"🌐 世界 #{getattr(world, 'id', '?')} 经历投递失败（非致命）: {e}")
        return 0
