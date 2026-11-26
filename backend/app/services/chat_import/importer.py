"""
把统一结构写进历史 —— 「导入的对话就是那个 AI 的一部分」这句话的落地点。

三条与聊天通道不同的规矩，都写在这里：

1. **不走 send_dm_message。** 那条路会做表情归一、好友校验、实时推送、未读累加，
   一次几千条既慢又会把会话刷成实时消息。导入是一次性补历史，直接批量写行。
2. **时间用原始时间。** 拿不到才退回导入时间；整段缺时间的按 1ms 递增排，
   否则几百条挤在同一秒，翻记录时顺序会随机。
3. **历史算已读。** read_at 直接置为导入时间，不把用户的未读数顶成 99+。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.dm import get_or_create_dm_session
from app.models.chat_import import ChatImport
from app.models.dm import DMMessage, DMSession
from app.models.message import Message
from app.services.chat_import.schema import Transcript

BATCH = 500
# 一条消息落成站内消息时用的通道标记：与 via='qq' 同一套机制，渲染侧只认这个值
VIA_IMPORT = "import"


def _attachment_payload(ref) -> dict:
    """统一结构的附件 → 站内附件元数据。

    没有原文件的只留元数据，并带 available=False：前端据此显示「无原文件」。
    字段名与平台附件同构（file_id/path/name/size/mime_type），多出来的两个字段
    其他渲染路径不认识也不影响——它们只看 name / mime_type。
    """
    data = {
        "name": ref.name,
        "size": ref.size,
        "mime_type": ref.mime_type,
        "type": ref.type or "file",
    }
    if ref.file_id:
        data["file_id"] = ref.file_id
    if ref.path:
        data["path"] = ref.path
    if not ref.available:
        data["available"] = False
        data["reason"] = ref.reason or "no_source"
    return data


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _inject_initial_summary(db: AsyncSession, agent, session_id: str, transcript: Transcript) -> bool:
    """「初步摘要注入」：往账本落一条 kind="summary"。

    为什么用账本而不是别的地方：用户要的位置就是 compact 摘要那个位置，而账本的
    summary 条目正是它。压缩（rewrite_context）是唯一重写点，它只保留「摘要 + 最近 N 条」，
    这条旧摘要自然被新摘要顶掉——**释放**是既有机制的副作用，压缩逻辑一行没改。

    顺序：此刻 AI 已经有一条开场私信（seq 更小），导入的消息还没同步进账本
    （要等它下次说话时 sync_dm_history 才补）。所以这条摘要落在「开场之后、历史之前」，
    正是「先说清楚我们在别处聊过，细节在下面的记录里」的读法。
    """
    text = (transcript.summary or "").strip()
    if not text:
        return False

    from app.services.history.context_sync import append_events, context_ref
    from app.utils.pure.history import make_entry

    await append_events(
        db, agent, context_ref(session_id=session_id),
        [make_entry("summary", text, ref="chat_import")],
    )
    return True


async def import_transcript(
    db: AsyncSession,
    *,
    agent,
    creator_user_id: int,
    transcript: Transcript,
) -> dict:
    """写入 DM 历史。调用方负责 commit（与创建 AI 的其他写入同一个事务）。"""
    if not agent or not getattr(agent, "user_id", None):
        raise ValueError("这个 AI 还没有自己的账号，无法承载历史")

    session_info = await get_or_create_dm_session(
        db, creator_user_id, int(agent.user_id), skip_friendship_check=True
    )
    session_id = session_info["session_id"]
    session = (await db.execute(
        select(DMSession).where(DMSession.session_id == session_id)
    )).scalar_one()

    now = _now()
    cursor = now
    rows: list[DMMessage] = []
    first_at: datetime | None = None
    last_at: datetime | None = None
    attachment_count = 0
    missing = 0

    for turn in transcript.messages:
        at = turn.at or cursor
        if turn.at:
            cursor = turn.at + timedelta(milliseconds=1)
        else:
            cursor = cursor + timedelta(milliseconds=1)
        first_at = at if first_at is None or at < first_at else first_at
        last_at = at if last_at is None or at > last_at else last_at

        payload = [_attachment_payload(a) for a in turn.attachments]
        attachment_count += len(payload)
        missing += sum(1 for a in payload if a.get("available") is False)

        content = (turn.content or "").strip()
        if not content:
            # DMMessage.content 非空：只有附件的消息给一句能读懂的话，不留空串
            content = "（只有附件，原文不可见）" if payload else "（空）"

        rows.append(DMMessage(
            session_id=session_id,
            sender_id=int(creator_user_id) if turn.role == "user" else int(agent.user_id),
            content=content,
            attachments=json.dumps(payload, ensure_ascii=False) if payload else None,
            message_type="normal",
            via=VIA_IMPORT,
            read_at=now,
            created_at=at,
        ))

    for i in range(0, len(rows), BATCH):
        db.add_all(rows[i:i + BATCH])
        await db.flush()

    # 会话列表的「最后一条」：只有导入的消息比现有记录更晚时才往前推，
    # 否则用户在导入之后又聊了几句，会被这批历史顶回旧消息
    if rows:
        latest = max(rows, key=lambda m: (m.created_at or now))
        if session.last_message_at is None or (latest.created_at or now) > session.last_message_at:
            session.last_message_at = latest.created_at
            session.last_message_id = latest.id

    # 初步摘要：在消息之后写，位置正好在「开场私信」与「将来同步进来的历史」之间
    summary_injected = await _inject_initial_summary(db, agent, session_id, transcript)

    record = ChatImport(
        agent_id=int(agent.id),
        creator_user_id=int(creator_user_id),
        session_id=session_id,
        platform=transcript.origin.platform or "unknown",
        parser=transcript.origin.parser or "unknown",
        parser_version=int(transcript.origin.parser_version or 1),
        source_url=transcript.origin.source_url,
        title=(transcript.title or None),
        message_count=len(rows),
        attachment_count=attachment_count,
        missing_attachment_count=missing,
        first_at=first_at,
        last_at=last_at,
    )
    db.add(record)
    await db.flush()

    return {
        "import_id": record.id,
        "session_id": session_id,
        "imported": len(rows),
        "attachments": attachment_count,
        "missing_attachments": missing,
        "summary_injected": summary_injected,
    }
