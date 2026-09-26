"""
私信（DM）服务 —— 纯 CRUD，不含 AI 决策逻辑

职责：私信会话的创建、查询、消息发送
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, update, func

from app.models.dm import DMSession, DMMessage
from app.models.user import User
from app.models.agent import Agent
from app.models.federation import FederatedEntity
from app.models.friendship import Friendship
from app.utils.pure.history import make_entry
from app.utils.pure.prompting import format_message, format_time_shanghai

logger = logging.getLogger(__name__)


def _dm_message_to_dict(m: DMMessage, sender_name: str, sender_type: str,
                        sender_avatar_url: str | None = None) -> dict:
    """将 DMMessage ORM 对象转为字典"""
    from app.utils.message_serializer import serialize_message
    return serialize_message(
        m,
        sender_name=sender_name,
        sender_type=sender_type,
        sender_avatar_url=sender_avatar_url,
        conversation_key='session_id',
        include_read_at=True,
    )


async def resolve_dm_sender_names(db, messages) -> dict[int, str]:
    """一次把私信里「谁在说话」查成名字（同一人只查一次）。"""
    names: dict[int, str] = {}
    for m in messages:
        if m.sender_id in names:
            continue
        u = await db.get(User, m.sender_id)
        names[m.sender_id] = (getattr(u, "username", "") or "").strip() or f"用户{m.sender_id}"
    return names


def dm_message_entry(message, *, agent_name: str, agent_user_id: int | None,
                     sender_name: str | None = None) -> dict:
    """一条私信 → 账本条目（**渲染即落库**：content 就是发给模型的最终字节）。

    附件名注入正文（对方发了什么文件，AI 得知道）；私信不截断正文（与旧路径同口径）。
    """
    content = message.content or ""
    if message.attachments:
        try:
            atts = json.loads(message.attachments) if isinstance(message.attachments, str) else message.attachments
            file_names = [a.get("name", a.get("path", "file")) for a in atts]
            desc = f"[文件: {', '.join(file_names)}]"
            content = f"{desc} {content}" if content else desc
        except (json.JSONDecodeError, TypeError):
            pass
    if getattr(message, "revoked_at", None):
        from app.utils.pure.history import revoked_text

        content = revoked_text()      # 撤回的只留占位（附件也不提了）
    is_self = message.sender_id == agent_user_id
    rendered = format_message({
        "time": format_time_shanghai(message.created_at),
        "speaker_name": sender_name or f"用户{message.sender_id}",
        "speaker_id": None if is_self else message.sender_id,
        "is_self": is_self,
        "content": content,
        "message_id": message.id,
    }, agent_name, max_content_len=-1)
    return make_entry("message", rendered, actor="self" if is_self else "user", ref=str(message.id))


async def _require_friendship(db: AsyncSession, user_a_id: int, user_b_id: int,
                              *, initiator_id: int | None = None):
    """校验这次私信是否放行。

    - system：永远放行（系统通知不拦）
    - 找 AI、AI 之间：放行（AI 公开可聊）
    - 人 → 人、AI → 人：必须互为好友。**"涉及 AI 一律放行"曾是漏洞**——AI 因此能给任意
      生人开新私信——按骚扰拒绝（AI 与它的主人本来就是好友，不受影响）
    - initiator_id 为空 = 在**已有会话**里发言：AI 参与即放行——会话是对方开的通道，
      QQ / 外部通道的被动回复靠这条活着
    """
    result = await db.execute(
        select(User.id, User.type).where(User.id.in_([user_a_id, user_b_id]))
    )
    types = {row[0]: row[1] for row in result.all()}
    if "system" in types.values():
        return
    if initiator_id is None and "ai" in types.values():
        return
    initiator = initiator_id if initiator_id is not None else user_a_id
    other = user_b_id if initiator == user_a_id else user_a_id
    if types.get(other) == "ai":
        return
    friendship = await db.execute(
        select(Friendship).where(
            ((Friendship.user_id == user_a_id) & (Friendship.friend_id == user_b_id) & (Friendship.friend_type == "human")) |
            ((Friendship.user_id == user_b_id) & (Friendship.friend_id == user_a_id) & (Friendship.friend_type == "human"))
        )
    )
    if friendship.first():
        return
    if types.get(initiator) == "ai":
        raise ValueError("你们还不是好友，不能主动私信生人。先用 send_friend_request 发好友申请，或等对方先来找你。")
    raise ValueError("你们还不是好友，无法发送私信。请先添加好友后再试。")


def generate_dm_session_id(user_a_id: int, user_b_id: int) -> str:
    """生成排序拼接的会话 ID（幂等）"""
    ids = sorted([user_a_id, user_b_id])
    return f"{ids[0]}_{ids[1]}"


async def get_or_create_dm_session(
    db: AsyncSession,
    current_user_id: int,
    target_user_id: int,
    skip_friendship_check: bool = False,
) -> dict:
    """获取或创建私信会话"""
    if current_user_id == target_user_id:
        raise ValueError("不能和自己私信")

    target = await db.execute(select(User).where(User.id == target_user_id))
    target_user = target.scalar_one_or_none()
    if target_user is None:
        raise ValueError("目标用户不存在")

    session_id = generate_dm_session_id(current_user_id, target_user_id)

    result = await db.execute(
        select(DMSession).where(DMSession.session_id == session_id)
    )
    session = result.scalar_one_or_none()

    is_new = False
    if session is None:
        if not skip_friendship_check:
            # 建新会话才判"谁在发起"：已有会话里的回复不受好友关系限制
            await _require_friendship(db, current_user_id, target_user_id,
                                      initiator_id=current_user_id)
        is_new = True
        user_ids = sorted([current_user_id, target_user_id])
        session = DMSession(
            session_id=session_id,
            user1_id=user_ids[0],
            user2_id=user_ids[1],
        )
        db.add(session)
        await db.flush()
        await db.refresh(session)

    partner = await _get_partner_info(db, target_user_id)

    return {
        "session_id": session.session_id,
        "is_new": is_new,
        "partner": partner,
    }


async def list_dm_sessions(db: AsyncSession, user_id: int) -> list[dict]:
    """获取用户的所有私信会话列表"""
    result = await db.execute(
        select(DMSession).where(
            or_(
                DMSession.user1_id == user_id,
                DMSession.user2_id == user_id,
            )
        ).order_by(DMSession.last_message_at.desc().nullslast())
    )
    sessions = result.scalars().all()

    dm_list = []
    for s in sessions:
        partner_id = s.user2_id if s.user1_id == user_id else s.user1_id
        partner = await _get_partner_info(db, partner_id)

        unread_result = await db.execute(
            select(func.count(DMMessage.id)).where(
                DMMessage.session_id == s.session_id,
                DMMessage.sender_id != user_id,
                DMMessage.read_at.is_(None),
            )
        )
        unread_count = unread_result.scalar() or 0

        my_dnd_until = s.user1_dnd_until if s.user1_id == user_id else s.user2_dnd_until

        last_msg = None
        if s.last_message_id:
            last_result = await db.execute(
                select(DMMessage).where(DMMessage.id == s.last_message_id)
            )
            msg = last_result.scalar_one_or_none()
            if msg:
                from app.utils.message_serializer import make_preview, mention_names
                from app.utils.text import render_mention_names

                names = await mention_names(db, [msg.content])
                last_msg = make_preview(
                    render_mention_names(msg.content or "", names), msg.attachments, max_len=100
                )

        fed_check = await db.execute(
            select(FederatedEntity).where(
                FederatedEntity.entity_type == "dm",
                FederatedEntity.local_ref_id == s.session_id,
                FederatedEntity.is_enabled == True,
            )
        )
        is_federated = fed_check.first() is not None

        dm_list.append({
            "session_id": s.session_id,
            "partner": partner,
            "last_message_preview": last_msg,
            "last_message_at": str(s.last_message_at) if s.last_message_at else None,
            "unread_count": unread_count,
            "my_dnd_until": str(my_dnd_until) if my_dnd_until else None,
            "is_federated": is_federated,
            "is_pinned": False,
        })

    return dm_list


async def get_dm_session(db: AsyncSession, session_id: str, user_id: int,
                          message_limit: int = 50, summary: bool = False) -> dict:
    """获取会话详情（summary=True 时跳过消息加载，仅返回元数据）"""
    result = await db.execute(
        select(DMSession).where(DMSession.session_id == session_id)
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise ValueError("会话不存在")
    if user_id not in (session.user1_id, session.user2_id):
        raise ValueError("无权访问此会话")

    partner_id = session.user2_id if session.user1_id == user_id else session.user1_id
    partner = await _get_partner_info(db, partner_id)

    if summary:
        messages = []
    else:
        await db.execute(
            update(DMMessage)
            .where(
                DMMessage.session_id == session_id,
                DMMessage.sender_id != user_id,
                DMMessage.read_at.is_(None),
            )
            .values(read_at=datetime.now(timezone.utc).replace(tzinfo=None))
        )
        messages = await _get_messages(db, session_id, limit=message_limit)

    my_dnd_until = session.user1_dnd_until if session.user1_id == user_id else session.user2_dnd_until

    fed_check = await db.execute(
        select(FederatedEntity).where(
            FederatedEntity.entity_type == "dm",
            FederatedEntity.local_ref_id == session_id,
            FederatedEntity.is_enabled == True,
        )
    )
    is_federated = fed_check.first() is not None

    return {
        "session_id": session.session_id,
        "partner": partner,
        "my_dnd_until": str(my_dnd_until) if my_dnd_until else None,
        "messages": messages,
        "is_federated": is_federated,
    }


async def get_dm_messages(db: AsyncSession, session_id: str, user_id: int,
                           limit: int = 50, before_id: int | None = None,
                           after_id: int | None = None) -> list[dict]:
    """获取私信消息列表（游标分页），同时标记已读"""
    result = await db.execute(
        select(DMSession).where(DMSession.session_id == session_id)
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise ValueError("会话不存在")
    if user_id not in (session.user1_id, session.user2_id):
        raise ValueError("无权访问此会话")

    await db.execute(
        update(DMMessage)
        .where(
            DMMessage.session_id == session_id,
            DMMessage.sender_id != user_id,
            DMMessage.read_at.is_(None),
        )
        .values(read_at=datetime.now(timezone.utc).replace(tzinfo=None))
    )

    return await _get_messages(db, session_id, limit=limit, before_id=before_id, after_id=after_id)


async def send_dm_message(
    db: AsyncSession,
    session_id: str,
    sender_id: int,
    content: str,
    reply_to: int | None = None,
    created_at: datetime | None = None,
    attachments: list[dict] | None = None,
    message_type: str = "normal",
    skip_friendship_check: bool = False,
) -> dict:
    """发送私信消息"""
    # AI 抄回来的 [msg_id=N] 收掉：标记是给它读的，N 属本会话就当成本意。
    from app.utils.text import take_trailing_msg_id

    content, echoed = take_trailing_msg_id(content)
    if echoed and reply_to is None:
        exists = (await db.execute(
            select(DMMessage.id).where(DMMessage.id == echoed, DMMessage.session_id == session_id)
        )).first()
        reply_to = echoed if exists else None
    if not content.strip() and not attachments:
        raise ValueError("消息内容不能为空")

    result = await db.execute(
        select(DMSession).where(DMSession.session_id == session_id)
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise ValueError("会话不存在")
    if sender_id not in (session.user1_id, session.user2_id):
        raise ValueError("无权在此会话中发言")

    receiver_id = session.user2_id if session.user1_id == sender_id else session.user1_id
    if not skip_friendship_check:
        await _require_friendship(db, sender_id, receiver_id)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    msg = DMMessage(
        session_id=session_id,
        sender_id=sender_id,
        content=content.strip(),
        reply_to=reply_to,
        attachments=json.dumps(attachments) if attachments else None,
        message_type=message_type,
        created_at=created_at or now,
    )
    db.add(msg)
    await db.flush()

    if attachments:
        from app.services.content.file_service import track_forward_reference
        for att in attachments:
            fid = att.get("file_id") if isinstance(att, dict) else getattr(att, "file_id", None)
            if fid:
                await track_forward_reference(db, fid, "human", sender_id)

    # 回复即阅读：标记对方未读消息为已读
    await db.execute(
        update(DMMessage)
        .where(
            DMMessage.session_id == session_id,
            DMMessage.sender_id != sender_id,
            DMMessage.read_at.is_(None),
        )
        .values(read_at=now)
    )

    session.last_message_id = msg.id
    session.last_message_at = now

    await db.flush()
    await db.refresh(msg)

    result = await db.execute(
        select(User.username, User.type, User.avatar_url).where(User.id == sender_id)
    )
    row = result.one_or_none()
    sender_name = row[0] if row else f"用户{sender_id}"
    sender_type = (row[1] or "human") if row else "human"
    sender_avatar_url = row[2] if row else None

    if sender_type == "ai" and not sender_avatar_url:
        agent_avatar_result = await db.execute(
            select(Agent.avatar_url).where(Agent.user_id == sender_id)
        )
        agent_avatar = agent_avatar_result.scalar()
        if agent_avatar:
            sender_avatar_url = agent_avatar

    payload = _dm_message_to_dict(msg, sender_name, sender_type, sender_avatar_url)

    # 私信出口：所有关心这条私信的通道从这里接出去（QQ 通道等）
    from app.chat.outbound import dispatch_dm_message

    await dispatch_dm_message(db, session_id, payload)

    return payload


async def set_dm_dnd(db: AsyncSession, session_id: str, user_id: int,
                     duration_minutes: int | None = None) -> dict:
    """设置私信免打扰"""
    result = await db.execute(
        select(DMSession).where(DMSession.session_id == session_id)
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise ValueError("会话不存在")
    if user_id not in (session.user1_id, session.user2_id):
        raise ValueError("无权操作此会话")

    dnd_until = None
    if duration_minutes is not None:
        dnd_until = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=duration_minutes)

    if session.user1_id == user_id:
        session.user1_dnd_until = dnd_until
    else:
        session.user2_dnd_until = dnd_until

    await db.flush()
    return {
        "session_id": session_id,
        "dnd_until": str(dnd_until) if dnd_until else None,
        "is_permanent": duration_minutes is None,
    }


async def cancel_dm_dnd(db: AsyncSession, session_id: str, user_id: int) -> dict:
    """取消私信免打扰"""
    result = await db.execute(
        select(DMSession).where(DMSession.session_id == session_id)
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise ValueError("会话不存在")
    if user_id not in (session.user1_id, session.user2_id):
        raise ValueError("无权操作此会话")

    if session.user1_id == user_id:
        session.user1_dnd_until = None
    else:
        session.user2_dnd_until = None

    await db.flush()
    return {"session_id": session_id, "dnd_until": None}


async def is_user_in_dm_dnd(db: AsyncSession, session_id: str, user_id: int) -> bool:
    """检查用户是否在此私信会话的免打扰中"""
    result = await db.execute(
        select(DMSession).where(DMSession.session_id == session_id)
    )
    session = result.scalar_one_or_none()
    if session is None:
        return False

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if session.user1_id == user_id:
        dnd = session.user1_dnd_until
    elif session.user2_id == user_id:
        dnd = session.user2_dnd_until
    else:
        return False

    if dnd is None:
        return False
    return dnd > now


# ============================================================
# 内部工具函数
# ============================================================

async def _get_partner_info(db: AsyncSession, user_id: int) -> dict:
    """获取用户信息（含在线状态，AI 则查 agent 表）"""
    user_result = await db.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one_or_none()
    if user is None:
        return {"id": user_id, "name": f"未知:{user_id}", "type": "unknown", "state": None}

    state = None
    avatar_url = getattr(user, 'avatar_url', None)
    status_text = getattr(user, 'status_text', None)
    status_color = getattr(user, 'status_color', None)
    if user.type == "ai":
        agent_result = await db.execute(
            select(Agent.state, Agent.avatar_url, Agent.status_text, Agent.status_color).where(Agent.user_id == user_id)
        )
        agent_row = agent_result.one_or_none()
        if agent_row:
            state = agent_row[0]
            avatar_url = agent_row[1] or avatar_url
            if agent_row[2]:
                status_text = agent_row[2]
            if agent_row[3]:
                status_color = agent_row[3]
    else:
        from app.services.infrastructure.online_tracker import get_user_online_status
        if get_user_online_status(user_id):
            state = "active"

    return {
        "id": user.id,
        "name": user.username,
        "type": user.type,
        "state": state,
        "avatar_url": avatar_url,
        "status_text": status_text,
        "status_color": status_color,
        "last_active_at": getattr(user, "last_active_at", None) and str(user.last_active_at),
    }


async def _get_user_name(db: AsyncSession, user_id: int) -> str:
    """获取用户名称"""
    result = await db.execute(select(User.username).where(User.id == user_id))
    name = result.scalar_one_or_none()
    return name or f"用户{user_id}"


async def _get_messages(db: AsyncSession, session_id: str, limit: int = 50,
                         before_id: int | None = None,
                         after_id: int | None = None) -> list[dict]:
    """获取消息列表（内部函数，支持双向游标分页）"""
    query = select(DMMessage).where(DMMessage.session_id == session_id)
    if before_id:
        query = query.where(DMMessage.id < before_id)
        query = query.order_by(DMMessage.created_at.desc())
    elif after_id:
        query = query.where(DMMessage.id > after_id)
        query = query.order_by(DMMessage.created_at.asc())
    else:
        query = query.order_by(DMMessage.created_at.desc())
    query = query.limit(limit)

    result = await db.execute(query)
    messages = result.scalars().all()

    sender_ids = {m.sender_id for m in messages}
    sender_info: dict[int, dict] = {}
    if sender_ids:
        result = await db.execute(
            select(User.id, User.username, User.type, User.avatar_url).where(User.id.in_(sender_ids))
        )
        for row in result.all():
            sender_info[row[0]] = {"name": row[1], "type": row[2] or "human", "avatar_url": row[3]}

        ai_sender_ids = [uid for uid, info in sender_info.items() if info["type"] == "ai"]
        if ai_sender_ids:
            agent_result = await db.execute(
                select(Agent.user_id, Agent.name, Agent.avatar_url).where(Agent.user_id.in_(ai_sender_ids))
            )
            for row in agent_result.all():
                if row[0] in sender_info:
                    sender_info[row[0]]["name"] = row[1] or sender_info[row[0]]["name"]
                    if row[2]:
                        sender_info[row[0]]["avatar_url"] = row[2]

    sorted_messages = sorted(messages, key=lambda m: m.id) if after_id else list(reversed(messages))

    return [
        _dm_message_to_dict(
            m,
            sender_name=sender_info.get(m.sender_id, {}).get("name", f"用户{m.sender_id}"),
            sender_type=sender_info.get(m.sender_id, {}).get("type", "human"),
            sender_avatar_url=sender_info.get(m.sender_id, {}).get("avatar_url"),
        )
        for m in sorted_messages
    ]
