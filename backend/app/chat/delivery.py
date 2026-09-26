"""
消息可达性管理 —— 消息能否送达的判断逻辑

职责：
  - DND 设置/取消/查询（群聊 + 私信）
  - 屏蔽（mute）查询
  - 暂存消息（pending messages）
  - 未读消息聚合

这是聊天世界的物理规则，不含 AI 决策逻辑。
人类和 AI 一视同仁通过这些规则判断消息是否能送达。
"""

import logging
from datetime import datetime, timedelta, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, update, desc, func as sqlfunc
from app.models.group import Group, GroupMember, MemberSilence
from app.models.message import PendingMessage, Message
from app.models.agent import Agent

logger = logging.getLogger(__name__)


# ============================================================
# 群聊 DND
# ============================================================

async def _agent_member(db: AsyncSession, agent_id: int, group_id: int,
                        member_type: str = "ai") -> GroupMember | None:
    """某个 AI（或人）在某个群里的成员行 —— 找这一段只有这一个入口。

    为什么收敛：member_id 存的是 user_id（v2.0 统一口径），每加一个开关（免打扰 / 屏蔽 /
    不允许穿透）都要重抄一遍"先按 agent.id 反查 user_id、再查成员行"，抄第三遍就是漂的开始。
    """
    lookup_id = agent_id
    if member_type == "ai":
        agent = await db.get(Agent, agent_id)
        if agent is None:
            agent = (await db.execute(
                select(Agent).where(Agent.user_id == agent_id)
            )).scalar_one_or_none()
        if agent:
            lookup_id = agent.user_id
    return (await db.execute(
        select(GroupMember).where(
            and_(
                GroupMember.group_id == group_id,
                GroupMember.member_type == member_type,
                GroupMember.member_id == lookup_id,
            )
        )
    )).scalar_one_or_none()


async def set_group_dnd(
    db: AsyncSession,
    agent_id: int,
    group_id: int,
    duration_minutes: int | None = None,
    member_type: str = "ai",
) -> GroupMember:
    """
    为群成员设置免打扰（支持 human 和 ai）。
    - duration_minutes = 0 或 None → 永久免打扰 (dnd_until = 2099-12-31)
    - duration_minutes > 0 → 临时免打扰

    免打扰与屏蔽是两件事（见 docs/chat_service/design/chat_service_design.md §4.2）：
    免打扰的 @/@all/群公告/特别关心**都穿透**；"连 @ 都不唤醒"归屏蔽（muted_until）。
    """
    member = await _agent_member(db, agent_id, group_id, member_type)
    if member is None:
        raise ValueError(f"用户 {agent_id} 不在群聊 {group_id} 中")

    if duration_minutes is not None and duration_minutes > 0:
        member.dnd_until = datetime.utcnow() + timedelta(minutes=duration_minutes)
        logger.info(f"用户 {agent_id} 在群聊 {group_id} 设置临时免打扰 {duration_minutes} 分钟")
    else:
        member.dnd_until = datetime(2099, 12, 31, 23, 59, 59)
        logger.info(f"用户 {agent_id} 在群聊 {group_id} 设置永久免打扰")

    await db.flush()
    return member


async def cancel_group_dnd(
    db: AsyncSession,
    agent_id: int,
    group_id: int,
    member_type: str = "ai",
) -> GroupMember:
    """取消群聊免打扰"""
    member = await _agent_member(db, agent_id, group_id, member_type)
    if member is None:
        raise ValueError(f"用户 {agent_id} 不在群聊 {group_id} 中")

    member.dnd_until = datetime(2000, 1, 1)
    await db.flush()
    logger.info(f"用户 {agent_id} 在群聊 {group_id} 已取消免打扰")
    return member


async def is_member_in_dnd(db: AsyncSession, agent_id: int, group_id: int) -> bool:
    """检查成员在指定群聊是否处于免打扰状态（整群被暂停也算）"""
    agent = await db.get(Agent, agent_id)
    if agent and agent.is_paused:
        return True

    member = await _agent_member(db, agent_id, group_id)
    if member is None or member.dnd_until is None:
        return False
    return member.dnd_until > datetime.utcnow()


async def is_member_muted(db: AsyncSession, agent_id: int, group_id: int) -> bool:
    """检查成员在指定群聊是否处于屏蔽状态（比 DND 更强，@/公告也不穿透）"""
    member = await _agent_member(db, agent_id, group_id)
    if member is None or member.muted_until is None:
        return False
    return member.muted_until > datetime.utcnow()


# ============================================================
# 按人静音（只对某个人不响应：连 @ 也不唤醒）
# ============================================================

def _silence_active(row: MemberSilence, now: datetime) -> bool:
    """这条静音现在还算数吗：时间没到点、条数没扣完（两个维度都空 = 永久）"""
    if row.until_at is not None and row.until_at <= now:
        return False
    if row.remaining_count is not None and row.remaining_count <= 0:
        return False
    return True


async def get_active_silence(db: AsyncSession, agent_id: int, group_id: int,
                             target_user_id: int) -> MemberSilence | None:
    """这个 AI 在这个群里现在静音着这个人吗 —— 是就把那一行交给调用方（它可能还要扣一次条数）"""
    row = (await db.execute(
        select(MemberSilence).where(
            MemberSilence.agent_id == agent_id,
            MemberSilence.group_id == group_id,
            MemberSilence.target_user_id == target_user_id,
        )
    )).scalar_one_or_none()
    if row is None or not _silence_active(row, datetime.utcnow()):
        return None
    return row


async def silence_member(db: AsyncSession, agent_id: int, group_id: int, target_user_id: int,
                         *, duration_minutes: int | None = None,
                         message_count: int | None = None) -> MemberSilence:
    """给某个人上静音。再设一次 = 覆盖（以这次的时长/条数为准，不叠加）"""
    row = (await db.execute(
        select(MemberSilence).where(
            MemberSilence.agent_id == agent_id,
            MemberSilence.group_id == group_id,
            MemberSilence.target_user_id == target_user_id,
        )
    )).scalar_one_or_none()
    if row is None:
        row = MemberSilence(agent_id=agent_id, group_id=group_id, target_user_id=target_user_id)
        db.add(row)
    row.until_at = (datetime.utcnow() + timedelta(minutes=int(duration_minutes))
                    if duration_minutes else None)
    row.remaining_count = int(message_count) if message_count else None
    await db.flush()
    logger.info(
        f"AI {agent_id} 在群 {group_id} 静音了用户 {target_user_id}"
        f"（{duration_minutes or '-'} 分钟 / {message_count or '-'} 条）"
    )
    return row


async def consume_silence(db: AsyncSession, row: MemberSilence) -> None:
    """扣一次条数（时间维度不用扣，自己会过期）——扣到 0 这条静音就失效了"""
    if row.remaining_count is not None:
        row.remaining_count = max(0, int(row.remaining_count) - 1)
        await db.flush()


async def cancel_member_silence(db: AsyncSession, agent_id: int, group_id: int,
                                target_user_id: int) -> bool:
    """取消按人静音（删行）。返回是否真有一条（幂等：没有也当成功）"""
    row = (await db.execute(
        select(MemberSilence).where(
            MemberSilence.agent_id == agent_id,
            MemberSilence.group_id == group_id,
            MemberSilence.target_user_id == target_user_id,
        )
    )).scalar_one_or_none()
    if row is None:
        return False
    await db.delete(row)
    await db.flush()
    logger.info(f"AI {agent_id} 在群 {group_id} 取消了静音用户 {target_user_id}")
    return True


# ============================================================
# 暂存消息 (Pending Messages)
# ============================================================

def delivery_decision(*, online: bool, in_dnd: bool, mentioned: bool) -> str:
    """群消息对某个 AI 成员的处置（**唯一口径**）：返回 "push" 或 "pending"。

    依据文档的可达性矩阵（docs/chat_service/design/chat_service_design.md §4.2）：

    | 情形 | 处置 |
    |------|------|
    | 在线且不 DND/暂停 | push |
    | 在线但 DND/暂停、**被 @** | push（@提及 穿透） |
    | 在线但 DND/暂停、没被 @ | pending |
    | **不在线（不论有没有被 @）** | pending（cpec.md：dnd/offline 则暂存） |

    「不在线也暂存」这一格以前是漏的：投递循环只遍历在线连接，离线成员没人管。
    """
    if online and (not in_dnd or mentioned):
        return "push"
    return "pending"


async def store_pending_message(
    db: AsyncSession,
    agent_id: int,
    group_id: int,
    message_id: int,
) -> PendingMessage:
    """将消息暂存到接收者的 pending 列表（离线/DND 时使用）"""
    pending = PendingMessage(
        agent_id=agent_id,
        group_id=group_id,
        message_id=message_id,
    )
    db.add(pending)
    await db.flush()
    await db.refresh(pending)
    return pending


async def get_pending_messages(
    db: AsyncSession,
    agent_id: int,
    group_id: int | None = None,
    unread_only: bool = True,
) -> list[dict]:
    """获取暂存消息，可按群聊过滤"""
    query = select(PendingMessage, Message).join(
        Message, PendingMessage.message_id == Message.id
    ).where(PendingMessage.agent_id == agent_id)

    if unread_only:
        query = query.where(PendingMessage.is_read == False)
    if group_id is not None:
        query = query.where(PendingMessage.group_id == group_id)

    query = query.order_by(desc(Message.created_at))

    result = await db.execute(query)
    rows = result.all()

    return [
        {
            "pending_id": pm.id,
            "group_id": pm.group_id,
            "message_id": pm.message_id,
            "content": msg.content,
            "sender_type": msg.sender_type,
            "sender_id": msg.sender_id,
            "created_at": str(msg.created_at) if msg.created_at else None,
        }
        for pm, msg in rows
    ]


async def mark_pending_read(
    db: AsyncSession,
    agent_id: int,
    group_id: int | None = None,
):
    """标记暂存消息为已读"""
    query = (
        update(PendingMessage)
        .where(PendingMessage.agent_id == agent_id)
        .where(PendingMessage.is_read == False)
    )
    if group_id is not None:
        query = query.where(PendingMessage.group_id == group_id)

    query = query.values(is_read=True)
    await db.execute(query)
    await db.flush()


# ============================================================
# 未读消息聚合
# ============================================================

async def check_unread(db: AsyncSession, agent_id: int) -> list[dict]:
    """
    获取各群聊的未读消息摘要（按群分组）。
    返回: [{group_id, group_name, unread_count, last_message_preview, last_message_at}, ...]
    """
    result = await db.execute(
        select(
            PendingMessage.group_id,
            sqlfunc.count(PendingMessage.id).label("unread_count"),
            sqlfunc.max(Message.created_at).label("last_message_at"),
        )
        .join(Message, PendingMessage.message_id == Message.id)
        .where(
            and_(
                PendingMessage.agent_id == agent_id,
                PendingMessage.is_read == False,
            )
        )
        .group_by(PendingMessage.group_id)
    )

    summaries = []
    for row in result:
        group_result = await db.execute(select(Group).where(Group.id == row.group_id))
        group = group_result.scalar_one_or_none()
        group_name = group.name if group else f"群聊#{row.group_id}"

        latest = await db.execute(
            select(Message.content)
            .join(PendingMessage, PendingMessage.message_id == Message.id)
            .where(
                and_(
                    PendingMessage.agent_id == agent_id,
                    PendingMessage.group_id == row.group_id,
                    PendingMessage.is_read == False,
                )
            )
            .order_by(Message.created_at.desc())
            .limit(1)
        )
        preview_row = latest.scalar_one_or_none()
        preview = preview_row[:100] if preview_row else "..."

        summaries.append({
            "group_id": row.group_id,
            "group_name": group_name,
            "unread_count": row.unread_count,
            "last_message_preview": preview,
            "last_message_at": str(row.last_message_at) if row.last_message_at else None,
        })

    return summaries


async def check_unread_dms(db: AsyncSession, agent_id: int) -> list[dict]:
    """AI 还没处理的私信（按会话聚合）。

    私信不另存一份"暂存"：**`dm_messages.read_at` 就是唯一真相** —— 对方打开会话、
    AI 回复（send_dm_message 的"回复即阅读"）都会把它标上。pending_messages 是群聊那条
    投递链的账本，私信再记一份等于同一件事两处真相，迟早对不上。
    所以这里只做"读出来"，不加表、不加迁移。
    """
    from app.models.dm import DMMessage, DMSession
    from app.models.user import User

    agent = (await db.execute(select(Agent).where(Agent.id == agent_id))).scalar_one_or_none()
    if agent is None or not agent.user_id:
        return []
    me = agent.user_id

    rows = (await db.execute(
        select(
            DMMessage.session_id,
            sqlfunc.count(DMMessage.id).label("unread_count"),
            sqlfunc.max(DMMessage.created_at).label("last_message_at"),
        )
        .join(DMSession, DMSession.session_id == DMMessage.session_id)
        .where(
            or_(DMSession.user1_id == me, DMSession.user2_id == me),
            DMMessage.sender_id != me,
            DMMessage.read_at.is_(None),
        )
        .group_by(DMMessage.session_id)
    )).all()

    summaries = []
    for session_id, unread_count, last_message_at in rows:
        session = (await db.execute(
            select(DMSession).where(DMSession.session_id == session_id)
        )).scalar_one_or_none()
        peer_id = None
        if session is not None:
            peer_id = session.user2_id if session.user1_id == me else session.user1_id
        peer_name = None
        if peer_id is not None:
            peer_name = (await db.execute(
                select(User.username).where(User.id == peer_id)
            )).scalar_one_or_none()
        preview = (await db.execute(
            select(DMMessage.content)
            .where(
                DMMessage.session_id == session_id,
                DMMessage.sender_id != me,
                DMMessage.read_at.is_(None),
            )
            .order_by(DMMessage.created_at.desc())
            .limit(1)
        )).scalar_one_or_none()

        summaries.append({
            "session_id": session_id,
            "peer_id": peer_id,
            "peer_name": peer_name or f"用户{peer_id}",
            "unread_count": unread_count,
            "last_message_preview": (preview or "")[:100] or None,
            "last_message_at": str(last_message_at) if last_message_at else None,
        })

    summaries.sort(key=lambda s: s["last_message_at"] or "", reverse=True)
    return summaries


async def generate_llm_summary(
    agent_id: int,
    group_id: int,
    group_name: str,
    unread_count: int,
    last_message_preview: str,
    api_base_url: str = "https://api.deepseek.com",
    api_key: str | None = None,
) -> str:
    """调用 LLM 生成自然语言摘要（骨架实现）"""
    if unread_count == 0:
        return f"群聊【{group_name}】没有新消息。"
    return (
        f"群聊【{group_name}】有 {unread_count} 条新消息，"
        f"最后一条：「{last_message_preview[:50]}」"
    )
