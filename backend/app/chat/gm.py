"""
群聊消息服务 —— 纯 CRUD，不含 AI 决策逻辑

职责：群聊创建、成员管理、消息收发
"""

import logging
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, and_, update, delete, func as sqlfunc
from app.models.group import Group, GroupMember
from app.models.message import Message
from app.models.agent import Agent as AgentModel
from app.utils.pure.history import make_entry
from app.utils.pure.prompting import format_message, format_time_shanghai

logger = logging.getLogger(__name__)

# 拥有群审批权的角色（审批入群申请 / 成员邀请）。审批权就是可见性：
# 只有这些角色能在「申请列表」里看到本群的待审项。
APPROVER_ROLES = ("owner", "admin")


# ============================================================
# 群聊 CRUD
# ============================================================

async def create_group(
    db: AsyncSession,
    name: str,
    owner_type: str,
    owner_id: int,
    initial_members: list[dict] | None = None,
) -> Group:
    """创建群聊"""
    from app.models.system_settings import SystemSettings
    ss_result = await db.execute(select(SystemSettings).where(SystemSettings.id == 1))
    ss = ss_result.scalar_one_or_none()
    default_limit = ss.default_concurrent_ai_limit if ss and ss.default_concurrent_ai_limit else 3

    group = Group(
        name=name,
        owner_type=owner_type,
        owner_id=owner_id,
        concurrent_ai_limit=default_limit,
    )
    db.add(group)
    await db.flush()
    await db.refresh(group)

    owner_member = GroupMember(
        group_id=group.id,
        member_type=owner_type,
        member_id=owner_id,
        role="owner",
    )
    db.add(owner_member)

    if initial_members:
        for member in initial_members:
            gm = GroupMember(
                group_id=group.id,
                member_type=member["type"],
                member_id=member["id"],
                role="member",
            )
            db.add(gm)

    await db.flush()
    logger.info(f"群聊 '{name}' (id={group.id}) 由 {owner_type}:{owner_id} 创建")
    return group


async def get_group(db: AsyncSession, group_id: int) -> Group | None:
    """获取群聊"""
    result = await db.execute(select(Group).where(Group.id == group_id))
    return result.scalar_one_or_none()


async def list_user_groups(db: AsyncSession, user_id: int) -> list[dict]:
    """列出用户所属的群聊（含未读信息、公告摘要等）"""
    from app.models.user import User

    result = await db.execute(
        select(GroupMember).where(
            GroupMember.member_type == "human",
            GroupMember.member_id == user_id,
        )
    )
    memberships = result.scalars().all()

    user_result = await db.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one_or_none()
    username = user.username if user else ""

    groups = []
    for m in memberships:
        group = await db.get(Group, m.group_id)
        if not group:
            continue

        announcement = None
        if group.announcement:
            announcement = group.announcement[:100] if len(group.announcement) > 100 else group.announcement

        dnd_until = str(m.dnd_until) if m.dnd_until else None

        unread_count = 0
        has_mention = False
        last_message_preview = None
        last_message_at = None

        read_baseline = m.last_read_at or m.joined_at
        if read_baseline:
            count_result = await db.execute(
                select(sqlfunc.count(Message.id)).where(
                    Message.group_id == group.id,
                    Message.created_at > read_baseline,
                    ~((Message.sender_type == "human") & (Message.sender_id == user_id)),
                )
            )
            unread_count = count_result.scalar() or 0

            if username and unread_count > 0:
                from app.utils.text import mention_token

                # 两种写法都算 @ 到我：新的是 <@!id>（入口归一之后正文里就是它），
                # 旧的是 @名字（历史消息）
                mention_result = await db.execute(
                    select(Message).where(
                        Message.group_id == group.id,
                        Message.created_at > read_baseline,
                        (
                            Message.content.contains(f"@{username}")
                            | Message.content.contains(mention_token(user_id))
                        ),
                    ).limit(1)
                )
                has_mention = mention_result.scalar_one_or_none() is not None

        last_msg_result = await db.execute(
            select(Message).where(
                Message.group_id == group.id,
            ).order_by(Message.created_at.desc()).limit(1)
        )
        last_msg = last_msg_result.scalar_one_or_none()
        if last_msg:
            last_message_at = str(last_msg.created_at) if last_msg.created_at else None
            from app.utils.message_serializer import make_preview, mention_names
            from app.utils.text import render_mention_names

            # 正文里存的是 <@!id>（聊天界面由前端渲染成名字）；这里是"顺手给人看"的地方，后端自己换
            names = await mention_names(db, [last_msg.content])
            preview = make_preview(
                render_mention_names(last_msg.content or "", names), last_msg.attachments, max_len=50
            )
            if last_msg.sender_type == "ai":
                a_result = await db.execute(select(AgentModel).where(AgentModel.user_id == last_msg.sender_id))
                a = a_result.scalar_one_or_none()
                sender = a.name if a else "AI"
            elif last_msg.sender_type == "system":
                sender = "系统"
            else:
                u = await db.get(User, last_msg.sender_id)
                sender = u.username if u else "用户"
            last_message_preview = f"{sender}: {preview}"

        member_avatars: list[str] = []
        try:
            # 仅 members 模式需要全部成员头像用于展示，其他模式取前4个做 2×2 网格
            avatar_mode = getattr(group, 'avatar_mode', 'default') or 'default'
            avatar_query = select(GroupMember).where(
                GroupMember.group_id == group.id,
            )
            if avatar_mode != 'members':
                avatar_query = avatar_query.limit(4)
            avatar_result = await db.execute(avatar_query)
            avatar_members = avatar_result.scalars().all()

            # members 模式下按 include_ai_in_avatar 过滤
            include_ai = getattr(group, 'include_ai_in_avatar', True)
            for am in avatar_members:
                if avatar_mode == 'members' and am.member_type == "ai" and not include_ai:
                    continue
                a_url = None
                if am.member_type == "ai":
                    a_result = await db.execute(
                        select(AgentModel).where(AgentModel.user_id == am.member_id)
                    )
                    a = a_result.scalar_one_or_none()
                    if a is None:
                        a_result = await db.execute(
                            select(AgentModel).where(AgentModel.id == am.member_id)
                        )
                        a = a_result.scalar_one_or_none()
                    a_url = getattr(a, 'avatar_url', None) if a else None
                else:
                    u = await db.get(User, am.member_id)
                    a_url = getattr(u, 'avatar_url', None) if u else None
                if a_url:
                    member_avatars.append(a_url)
        except Exception:
            logger.warning("获取群聊 %d 成员头像失败，跳过", group.id, exc_info=True)

        groups.append({
            "id": group.id,
            "name": group.name,
            "owner_type": group.owner_type,
            "owner_id": group.owner_id,
            "is_vector_accelerated": group.is_vector_accelerated,
            "announcement": announcement,
            "speak_limit_per_minute": group.speak_limit_per_minute or 0,
            "speak_limit_window_seconds": group.speak_limit_window_seconds or 120,
            "my_role": m.role,
            "unread_count": unread_count,
            "has_mention": has_mention,
            "last_message_preview": last_message_preview,
            "last_message_at": last_message_at,
            "dnd_until": dnd_until,
            "member_avatars": member_avatars,
            "avatar_mode": group.avatar_mode or "default",
            "avatar_url": group.avatar_url,
            "include_ai_in_avatar": group.include_ai_in_avatar,
            # 发现与入群三开关：群设置面板据此回显，不能只在 PATCH 响应里给
            "searchable": bool(group.searchable),
            "auto_approve_join": bool(group.auto_approve_join),
            "approve_invites": bool(group.approve_invites),
            "is_pinned": False,
            "created_at": str(group.created_at) if group.created_at else None,
        })
    return groups


async def add_member(
    db: AsyncSession,
    group_id: int,
    member_type: str,
    member_id: int,
    role: str = "member",
) -> GroupMember:
    """添加群成员。AI 类型自动用 user_id 作为 member_id，统一 ID 空间。"""
    resolved_type = member_type
    resolved_id = member_id

    if member_type == "ai":
        # v2.0.0: 调用方传 user_id（/search 统一返回 user_id）；fallback agent.id 兼容旧调用
        agent_result = await db.execute(
            select(AgentModel).where(AgentModel.user_id == member_id)
        )
        agent = agent_result.scalar_one_or_none()
        if agent is None:
            agent = await db.get(AgentModel, member_id)
        if agent and agent.user_id:
            resolved_id = agent.user_id
        else:
            raise ValueError(f"AI agent {member_id} 没有关联的 user_id")

    result = await db.execute(
        select(GroupMember).where(
            GroupMember.group_id == group_id,
            GroupMember.member_id == resolved_id,
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        if existing.member_type != resolved_type:
            if existing.member_type != "ai":
                existing.member_type = resolved_type
                await db.flush()
        return existing

    member = GroupMember(
        group_id=group_id,
        member_type=resolved_type,
        member_id=resolved_id,
        role=role,
    )
    db.add(member)
    await db.flush()
    if resolved_type == "human":
        from app.services.world.decision_skill import emit_member_event
        await emit_member_event(db, group_id, "member_join", resolved_id)
    return member


async def get_group_members(db: AsyncSession, group_id: int) -> list[GroupMember]:
    """获取群成员列表"""
    result = await db.execute(
        select(GroupMember).where(GroupMember.group_id == group_id)
    )
    return list(result.scalars().all())


# ============================================================
# 消息操作
# ============================================================

async def send_gm_message(
    db: AsyncSession,
    group_id: int,
    sender_type: str,
    sender_id: int,
    content: str,
    reply_to: int | None = None,
    attachments: list[dict] | None = None,
    source: str = "user",
    allow_non_member: bool = False,
    via: str | None = None,
) -> Message:
    """创建消息（支持附件，非 owner 发送含附件消息时自动创建转发引用）

    source："user"=人/工具发起（会触发群消息钩子→世界程序感知）；
           "world"=世界程序/世界 AI 自己发的（不触发，防死循环）
    via：消息入口通道（NULL=站内，qq=QQ 通道）——只用于界面画"来源"标识，不影响投递

    安全（2026-08-05 产品发现）：非群成员默认禁止发消息——
    任意登录用户不能给任意群灌水；allow_non_member=True 供
    世界 AI 给绑定群发消息（群绑定校验在 world_tools 层已有）。
    """
    if not allow_non_member and not await is_group_member(db, group_id, sender_type, sender_id):
        raise ValueError("你不是该群成员，无法发送消息")
    # 入口归一：@名字 → <@!id>。人的手打、QQ 入站、工具调用都经这里，
    # 所以下游（唤醒判定、AI 上下文、通道出口）不必再各写一套名字比对
    content = await link_group_mentions(db, group_id, content)
    # 同上一条的道理：上下文里的 [msg_id=N] 标记被抄进正文时收掉，
    # N 确实是本群的消息就当成本意——它本来就是要"回复那条"
    from app.utils.text import take_trailing_msg_id

    content, echoed = take_trailing_msg_id(content)
    if echoed and reply_to is None:
        exists = (await db.execute(
            select(Message.id).where(Message.id == echoed, Message.group_id == group_id)
        )).first()
        reply_to = echoed if exists else None
    message = Message(
        group_id=group_id,
        sender_type=sender_type,
        sender_id=sender_id,
        content=content,
        reply_to=reply_to,
        attachments=attachments,
        via=via,
    )
    db.add(message)
    await db.flush()

    if attachments:
        from app.services.content.file_service import track_forward_reference
        for att in attachments:
            fid = att.get("file_id") if isinstance(att, dict) else getattr(att, "file_id", None)
            if fid:
                await track_forward_reference(db, fid, sender_type, sender_id)

    await db.refresh(message)

    # 群消息出口：所有关心这条消息的消费者从这里接出去（世界感知、QQ 通道…）
    from app.chat.outbound import dispatch_group_message

    await dispatch_group_message(db, group_id, message, source)

    return message


async def is_group_admin(db: AsyncSession, group_id: int, user_id: int) -> bool:
    """这个人是不是这个群的主人/管理员（撤回别人的消息、审批入群都认这一份）"""
    from app.models.group import GroupMember

    member = (await db.execute(
        select(GroupMember).where(
            GroupMember.group_id == group_id,
            GroupMember.member_type == "human",
            GroupMember.member_id == user_id,
        )
    )).scalar_one_or_none()
    return bool(member and member.role in APPROVER_ROLES)


async def is_group_member(db: AsyncSession, group_id: int, sender_type: str, sender_id: int) -> bool:
    """发送者是否为群成员（human/ai 两种成员类型）"""
    from app.models.group import GroupMember
    row = (await db.execute(
        select(GroupMember).where(
            GroupMember.group_id == group_id,
            GroupMember.member_type == sender_type,
            GroupMember.member_id == sender_id,
        )
    )).scalar_one_or_none()
    return row is not None


async def get_gm_messages(
    db: AsyncSession,
    group_id: int,
    limit: int = 20,
    before_id: int | None = None,
    after_id: int | None = None,
    after_time: datetime | None = None,
) -> list[Message]:
    """获取群聊消息（支持游标分页 + 按时间过滤未读）"""
    query = select(Message).where(Message.group_id == group_id)

    if after_time:
        query = query.where(Message.created_at > after_time)
        query = query.order_by(desc(Message.created_at))
    elif before_id:
        query = query.where(Message.id < before_id)
    elif after_id:
        query = query.where(Message.id > after_id)
        query = query.order_by(desc(Message.created_at))
    else:
        query = query.order_by(desc(Message.created_at))

    query = query.limit(limit)
    result = await db.execute(query)
    messages = list(result.scalars().all())

    if not after_id:
        messages = list(reversed(messages))
    return messages


def gm_message_to_dict(message: Message, sender_name: str | None = None,
                    sender_avatar_url: str | None = None,
                    sender_state: str | None = None) -> dict:
    """将 Message ORM 对象转为字典"""
    from app.utils.message_serializer import serialize_message
    return serialize_message(
        message,
        sender_name=sender_name,
        sender_avatar_url=sender_avatar_url,
        sender_state=sender_state,
        conversation_key='group_id',
        include_read_at=False,
    )


async def resolve_speaker_names(db, messages) -> dict[tuple[str, int], str]:
    """一次把「谁在说话」查成名字。

    本地消息的 sender_name 列是空的（网页端靠 sender_id 自己查名字），可 AI 的上下文
    必须有人名：空名字会被 format_message 原样渲染成字面 "None"，模型真的会把 None
    当成一个可 @ 的人（用户 2026-09-25 在 QQ 群里看到 AI 回 "@None"）。
    联邦消息自带 sender_name，优先用它；AI 的 sender_id 也是它的用户行 id，所以一张表查得到。
    """
    from app.models.user import User  # 函数内导入：与其它模型引用保持一致，避免循环导入

    names: dict[tuple[str, int], str] = {}
    for m in messages:
        key = (m.sender_type, m.sender_id)
        if key in names:
            continue
        name = (getattr(m, "sender_name", None) or "").strip()
        if not name:
            u = await db.get(User, m.sender_id)
            name = (getattr(u, "username", "") or "").strip()
        names[key] = name or f"用户{m.sender_id}"
    return names


async def resolve_member_ids(db, group_id: int) -> dict[str, int]:
    """群成员的「名字 → users.id」——入口把 @名字 归一成 <@!id> 要用它。

    和 resolve_speaker_names 是同一条口径的两面（那边 id→名字给 AI 看，这边名字→id 给入口用）；
    放同一个文件是因为「群成员怎么取」只该有一处。
    """
    from app.models.group import GroupMember
    from app.models.user import User

    rows = (await db.execute(
        select(User.id, User.username)
        .join(GroupMember, GroupMember.member_id == User.id)
        .where(GroupMember.group_id == group_id)
    )).all()
    return {str(name): int(uid) for uid, name in rows if name}


async def link_group_mentions(db, group_id: int, content: str) -> str:
    """群消息入口的 @ 归一：@名字 → <@!id>。

    正文里连 @ 都没有就别查库了——绝大多数消息没有 @。
    """
    from app.utils.text import link_mentions

    if not content or "@" not in content:
        return content
    return link_mentions(content, await resolve_member_ids(db, group_id))


def gm_message_entry(message, *, agent_name: str, agent_user_id: int | None,
                     speaker_name: str | None = None, max_len: int | None = None) -> dict:
    """一条群消息 → 账本条目（**渲染即落库**：content 就是发给模型的最终字节）。

    同一列消息每次渲染必须字节一致——差一个字符，从它开始的前缀全部 miss（§0）。
    """
    from app.utils.pure.history import FOLD_LIMIT, fold_text, revoked_text

    if getattr(message, "revoked_at", None):
        # 撤回的只渲染占位（原文留在库里、但不再进上下文）——
        # "撤回后才进历史"的那些因此天然只有占位，不用另外判断
        content = revoked_text()
    else:
        # 超长消息折成「前 75% + 省略标记 + 后 25%」：只截前半会让 AI 对自己的话失忆
        content = fold_text(
            message.content or "", limit=FOLD_LIMIT if max_len is None else max_len, expand_id=message.id
        )
    is_self = message.sender_type == "ai" and message.sender_id == agent_user_id
    rendered = format_message({
        "time": format_time_shanghai(message.created_at),
        "speaker_name": speaker_name or "未知",
        "speaker_id": None if is_self else message.sender_id,
        "is_self": is_self,
        "content": content,
        "message_id": message.id,
    }, agent_name, max_content_len=5000)
    return make_entry("message", rendered, actor="self" if is_self else "user", ref=str(message.id))


async def count_messages_between(db, group_id: int, *, after_id: int, before_id: int) -> int:
    """数一下 (after_id, before_id) 之间有多少条——缺口条目要报的数（after_id=0 表示从头数）。"""
    query = select(sqlfunc.count(Message.id)).where(Message.group_id == group_id)
    if after_id:
        query = query.where(Message.id > after_id)
    if before_id:
        query = query.where(Message.id < before_id)
    return (await db.execute(query)).scalar() or 0


# ============================================================
# 成员查询与操作
# ============================================================

async def is_member_of_group(
    db: AsyncSession,
    member_id: int,
    member_type: str,
    group_id: int,
) -> bool:
    """检查成员是否在指定群聊中。"""
    lookup_id = member_id
    if member_type == "ai":
        agent_result = await db.execute(
            select(AgentModel).where(AgentModel.user_id == member_id)
        )
        agent = agent_result.scalar_one_or_none()
        if agent is None:
            agent = await db.get(AgentModel, member_id)
        if agent and agent.user_id:
            lookup_id = agent.user_id

    result = await db.execute(
        select(GroupMember).where(
            and_(
                GroupMember.group_id == group_id,
                GroupMember.member_type == member_type,
                GroupMember.member_id == lookup_id,
            )
        )
    )
    return result.scalar_one_or_none() is not None


async def remove_member(
    db: AsyncSession,
    group_id: int,
    operator_id: int,
    target_type: str,
    target_id: int,
) -> None:
    """将成员踢出群聊"""
    operator = await _get_member(db, group_id, "human", operator_id)
    if operator is None or operator.role not in ("owner", "admin"):
        raise ValueError("仅群主或管理员可踢人")
    if target_type == operator.member_type and target_id == operator_id:
        raise ValueError("不能踢自己")
    target = await _get_member(db, group_id, target_type, target_id)
    if target is None:
        raise ValueError("该成员不在群内")
    if target.role == "owner":
        raise ValueError("不能踢群主")
    if operator.role == "admin" and target.role == "admin":
        raise ValueError("管理员不能踢其他管理员")
    await db.delete(target)
    await db.flush()
    if target_type == "human":
        from app.services.world.decision_skill import emit_member_event
        await emit_member_event(db, group_id, "member_leave", target_id, operator_id=operator_id)
    logger.info(f"成员 {target_type}:{target_id} 已被踢出群聊 {group_id}")


async def leave_group(
    db: AsyncSession,
    group_id: int,
    member_type: str,
    member_id: int,
) -> None:
    """退出群聊"""
    member = await _get_member(db, group_id, member_type, member_id)
    if member is None:
        raise ValueError("你不在该群聊中")
    if member.role == "owner":
        group = await db.get(Group, group_id)
        if group and not group.name.startswith("DM:"):
            raise ValueError("群主不能退群，请先将群主转让给其他成员")
    await db.delete(member)
    await db.flush()
    if member_type == "human":
        from app.services.world.decision_skill import emit_member_event
        await emit_member_event(db, group_id, "member_leave", member_id)
    logger.info(f"成员 {member_type}:{member_id} 已退出群聊 {group_id}")


async def update_last_read(db: AsyncSession, group_id: int, member_type: str, member_id: int) -> bool:
    """更新成员的最后阅读时间"""
    member = await _get_member(db, group_id, member_type, member_id)
    if member:
        member.last_read_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await db.flush()
        return True
    logger.warning(f"update_last_read: member not found group={group_id} {member_type}={member_id}")
    return False


async def get_approver_group_ids(db: AsyncSession, user_id: int) -> list[int]:
    """我拥有审批权的群（群主/管理员）。红点统计与申请列表共用一个口径。"""
    rows = await db.execute(
        select(GroupMember.group_id).where(
            GroupMember.member_type == "human",
            GroupMember.member_id == user_id,
            GroupMember.role.in_(APPROVER_ROLES),
        )
    )
    return [row[0] for row in rows.all()]


async def require_group_approver(db: AsyncSession, group_id: int, user_id: int) -> GroupMember:
    """校验用户对该群有审批权（群主/管理员），返回其成员记录。

    申请审批、邀请审批都从这里取权，避免各端点各写一遍角色判断。
    """
    member = await _get_member(db, group_id, "human", user_id)
    if member is None or member.role not in APPROVER_ROLES:
        raise ValueError("仅群主或管理员可审批")
    return member


async def update_group_settings(db: AsyncSession, group_id: int, operator_id: int, updates: dict) -> Group:
    """更新群聊设置"""
    group = await db.get(Group, group_id)
    if group is None:
        raise ValueError("群聊不存在")
    member = await _get_member(db, group_id, "human", operator_id)
    if member is None or member.role not in ("owner", "admin"):
        raise ValueError("仅群主或管理员可修改群设置")

    allowed_fields = {
        "name", "announcement",
        "speak_limit_per_minute", "speak_limit_window_seconds",
        "is_vector_accelerated",
        "avatar_mode", "avatar_url", "include_ai_in_avatar",
        # 发现与入群三开关
        "searchable", "auto_approve_join", "approve_invites",
    }
    for key, value in updates.items():
        if key not in allowed_fields:
            continue
        if key == "announcement" and value is not None:
            group.announcement = value
            group.announcement_updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        elif hasattr(group, key):
            setattr(group, key, value)

    await db.flush()
    if "name" in updates:
        from app.services.federation.federation_service import enqueue_profile_update
        await enqueue_profile_update(db, "group", group_id, "display_name", updates["name"])
    logger.info(f"群聊 {group_id} 设置已更新: {list(updates.keys())}")
    return group


async def change_member_role(db: AsyncSession, group_id: int, operator_id: int,
                              target_type: str, target_id: int, new_role: str) -> GroupMember:
    """修改成员角色"""
    if new_role not in ("admin", "member"):
        raise ValueError("角色只能是 admin 或 member")
    operator = await _get_member(db, group_id, "human", operator_id)
    if operator is None or operator.role != "owner":
        raise ValueError("仅群主可修改成员角色")
    if target_type == operator.member_type and target_id == operator_id:
        raise ValueError("不能修改自己的角色")
    target = await _get_member(db, group_id, target_type, target_id)
    if target is None:
        raise ValueError("该成员不在群内")
    if target.role == "owner":
        raise ValueError("不能修改群主的角色")
    target.role = new_role
    await db.flush()
    logger.info(f"群 {group_id} 成员 {target_type}:{target_id} 角色变更为 {new_role}")
    return target


async def disband_group(db: AsyncSession, group_id: int, operator_id: int) -> Group:
    """解散群聊"""
    group = await db.get(Group, group_id)
    if group is None:
        raise ValueError("群聊不存在")
    member = await _get_member(db, group_id, "human", operator_id)
    if member is None or member.role != "owner":
        raise ValueError("仅群主可解散群聊")
    from app.models.memory import RoughMemory
    await db.execute(update(RoughMemory).where(RoughMemory.group_id == group_id).values(group_id=None))
    from app.models.conversation_log import ConversationLog
    await db.execute(delete(ConversationLog).where(ConversationLog.group_id == group_id))
    await db.delete(group)
    await db.flush()
    logger.info(f"群聊 '{group.name}' (id={group_id}) 已被群主 {operator_id} 解散")
    return group


# ============================================================
# 内部辅助函数
# ============================================================

async def _get_member(db: AsyncSession, group_id: int, member_type: str, member_id: int) -> GroupMember | None:
    """获取群成员记录。"""
    result = await db.execute(
        select(GroupMember).where(
            GroupMember.group_id == group_id,
            GroupMember.member_type == member_type,
            GroupMember.member_id == member_id,
        )
    )
    member = result.scalar_one_or_none()
    if member is None and member_type == "ai":
        agent_result = await db.execute(
            select(AgentModel).where(AgentModel.user_id == member_id)
        )
        agent = agent_result.scalar_one_or_none()
        if agent is None:
            agent = await db.get(AgentModel, member_id)
        if agent and agent.user_id and agent.user_id != member_id:
            result = await db.execute(
                select(GroupMember).where(
                    GroupMember.group_id == group_id,
                    GroupMember.member_type == member_type,
                    GroupMember.member_id == agent.user_id,
                )
            )
            member = result.scalar_one_or_none()
    return member


# ============================================================
# 群公告 & 未读信息
# ============================================================

async def set_announcement(
    db: AsyncSession,
    group_id: int,
    content: str,
    operator_id: int,
) -> str:
    """
    设置群公告，返回公告内容。
    仅群主或管理员可操作。
    """
    group = await db.get(Group, group_id)
    if group is None:
        raise ValueError("群聊不存在")

    member = await _get_member(db, group_id, "human", operator_id)
    if member is None or member.role not in ("owner", "admin"):
        raise ValueError("仅群主或管理员可设置群公告")

    group.announcement = content
    group.announcement_updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.flush()
    logger.info(f"群聊 {group_id} 公告已更新")
    return content


async def delete_announcement(
    db: AsyncSession,
    group_id: int,
    operator_id: int,
) -> None:
    """删除群公告"""
    group = await db.get(Group, group_id)
    if group is None:
        raise ValueError("群聊不存在")

    member = await _get_member(db, group_id, "human", operator_id)
    if member is None or member.role not in ("owner", "admin"):
        raise ValueError("仅群主或管理员可删除群公告")

    group.announcement = None
    group.announcement_updated_at = None
    await db.flush()


async def get_unread_info(
    db: AsyncSession,
    group_id: int,
    user_id: int,
) -> dict:
    """
    获取用户在指定群聊的未读信息。

    返回: {unread_count, has_mention, has_announcement, last_message}
    """
    # 获取成员记录，查 last_read_at
    member = await _get_member(db, group_id, "human", user_id)
    last_read = member.last_read_at if member else None

    # 统计未读消息数（last_read 为 NULL 时用 joined_at 兜底）
    read_baseline = last_read or (member.joined_at if member else None)
    base_query = select(Message).where(Message.group_id == group_id)
    if read_baseline:
        base_query = base_query.where(Message.created_at > read_baseline)

    # 排除自己的消息
    base_query = base_query.where(
        ~((Message.sender_type == "human") & (Message.sender_id == user_id))
    )

    unread_result = await db.execute(base_query.order_by(Message.created_at.desc()))
    unread_messages = unread_result.scalars().all()

    unread_count = len(unread_messages)

    # 检查是否有 @提及
    has_mention = False
    user_name = None
    from app.models.user import User
    user = await db.get(User, user_id)
    if user:
        user_name = user.username
        for msg in unread_messages:
            if f"@{user_name}" in msg.content:
                has_mention = True
                break

    # 检查是否有未读公告
    group = await db.get(Group, group_id)
    has_announcement = False
    if group and group.announcement and group.announcement_updated_at:
        if last_read is None or group.announcement_updated_at > last_read:
            has_announcement = True

    # 最后一条消息
    last_msg = unread_messages[0] if unread_messages else None
    last_message = None
    if last_msg:
        sender_name = getattr(last_msg, "sender_name", None) or "未知"
        if last_msg.sender_type == "human":
            u = await db.get(User, last_msg.sender_id)
            if u:
                sender_name = u.username
        else:
            a_result2 = await db.execute(select(AgentModel).where(AgentModel.user_id == last_msg.sender_id))
            a = a_result2.scalar_one_or_none()
            if a:
                sender_name = a.name

        last_message = {
            "content": last_msg.content[:100],
            "sender_name": sender_name,
            "created_at": str(last_msg.created_at) if last_msg.created_at else None,
        }

    return {
        "unread_count": unread_count,
        "has_mention": has_mention,
        "has_announcement": has_announcement,
        "last_message": last_message,
    }
