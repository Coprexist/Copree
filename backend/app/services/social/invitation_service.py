"""
群邀请服务：纯函数 + 服务编排

邀请人类 → 发特殊 DM 卡片（message_type='group_invitation'），对方点接受才入群。
AI 成员不在此模块处理——直接走 add_member 入群。
"""
import json
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.gm import APPROVER_ROLES
from app.services.infrastructure.notification_service import (
    GROUP_INVITE_ACCEPTED, GROUP_INVITE_APPROVED, GROUP_INVITE_DECLINED, GROUP_INVITE_DENIED,
    notify_user, push_requests_changed,
)
from app.repositories.invitation_repo import InvitationRepository
from app.models.group import GroupInvitation, GroupMember
from app.models.user import User

logger = logging.getLogger(__name__)


def _now() -> datetime:
    """库里的时间列都是 naive UTC（历史口径），时间统一从这里取"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ═══════════════════════════════════════════════════════════════
# 纯函数：数据变换，无副作用
# ═══════════════════════════════════════════════════════════════

def build_invitation_card_content(
    group_name: str,
    inviter_name: str,
    message: str | None = None,
) -> str:
    """生成邀请卡片 DM 的文本内容（纯函数）"""
    lines = [
        f"📨 **群聊邀请**",
        f"",
        f"**{inviter_name}** 邀请你加入群聊「**{group_name}**」",
    ]
    if message:
        lines.append(f"")
        lines.append(f"> {message}")
    return "\n".join(lines)


def build_invitation_attachments(
    invitation_id: int,
    group_name: str,
    inviter_name: str,
    status: str = "pending",
) -> list[dict]:
    """构建邀请卡片的 attachments 载荷（纯函数）。
    前端据此渲染 InvitationCard 组件。
    """
    return [{
        "type": "group_invitation",
        "invitation_id": invitation_id,
        "group_name": group_name,
        "inviter_name": inviter_name,
        "status": status,
    }]


def needs_approval(group, inviter_role: str | None) -> bool:
    """群成员的邀请是否需要先过审批（纯函数）。

    群主/管理员的邀请免审：他们本来就有审批权，让自己的邀请再等自己批一遍没有意义。
    """
    return bool(group.approve_invites) and inviter_role not in APPROVER_ROLES


def _valid_transitions() -> dict[str, set[str]]:
    """邀请状态允许的转换（纯函数）"""
    return {
        "pending": {"accepted", "rejected"},
        "accepted": set(),
        "rejected": set(),
    }


def validate_invitation_transition(current_status: str, new_status: str) -> bool:
    """校验状态转换是否合法（纯函数）"""
    allowed = _valid_transitions().get(current_status, set())
    return new_status in allowed


def format_invitation_for_api(
    invitation: GroupInvitation,
    group_name: str,
    inviter_name: str,
    invitee_name: str,
) -> dict:
    """邀请记录 → API 响应 dict（纯函数）"""
    return {
        "id": invitation.id,
        "group_id": invitation.group_id,
        "group_name": group_name,
        "inviter_id": invitation.inviter_id,
        "inviter_name": inviter_name,
        "invitee_id": invitation.invitee_id,
        "invitee_name": invitee_name,
        "status": invitation.status,
        "message": invitation.message,
        "created_at": str(invitation.created_at) if invitation.created_at else None,
        "resolved_at": str(invitation.resolved_at) if invitation.resolved_at else None,
    }


# ═══════════════════════════════════════════════════════════════
# 服务编排：带副作用
# ═══════════════════════════════════════════════════════════════

async def create_group_invitation(
    invitation_repo: InvitationRepository,
    group_id: int,
    inviter_id: int,
    invitee_id: int,
    message: str | None = None,
) -> GroupInvitation:
    """只建邀请记录：判定要不要审批，写好 approval_status。

    不发 DM、不打扰被邀请人——免审链路与「等审批」链路共用这一步。
    """
    from app.models.group import Group

    # 检查是否已有待处理邀请（幂等防重）
    existing = await invitation_repo.execute(
        select(GroupInvitation).where(
            GroupInvitation.group_id == group_id,
            GroupInvitation.invitee_id == invitee_id,
            GroupInvitation.status == "pending",
        )
    )
    if existing.scalar_one_or_none():
        raise ValueError("该用户已有待处理的群邀请，请等待对方处理后再试")

    group = await invitation_repo.get(Group, group_id)
    if group is None:
        raise ValueError("群聊不存在")

    inviter_role = (await invitation_repo.execute(
        select(GroupMember.role).where(
            GroupMember.group_id == group_id,
            GroupMember.member_type == "human",
            GroupMember.member_id == inviter_id,
        )
    )).scalar_one_or_none()

    invitation = GroupInvitation(
        group_id=group_id,
        inviter_id=inviter_id,
        invitee_id=invitee_id,
        status="pending",
        message=message,
        approval_status="pending" if needs_approval(group, inviter_role) else "approved",
    )
    invitation_repo.add(invitation)
    await invitation_repo.flush()
    await invitation_repo.refresh(invitation)
    return invitation


async def notify_invitee(
    invitation_repo: InvitationRepository,
    invitation: GroupInvitation,
) -> dict:
    """给被邀请人发邀请卡片 DM。

    免审邀请在建记录后立刻走这里；需审批的邀请等群主/管理员批准后才走——
    被邀请人不该先收到一张群还没认可的卡片。
    """
    from app.chat.dm import get_or_create_dm_session, send_dm_message
    from app.models.group import Group

    group = await invitation_repo.get(Group, invitation.group_id)
    if group is None:
        raise ValueError("群聊不存在")
    inviter_name = await _username(invitation_repo, invitation.inviter_id)

    # 获取或创建 DM 会话（跳过好友校验——群邀请不要求已是好友）
    dm_session = await get_or_create_dm_session(
        invitation_repo.session, invitation.inviter_id, invitation.invitee_id,
        skip_friendship_check=True,
    )

    content = build_invitation_card_content(group.name, inviter_name, invitation.message)
    attachments = build_invitation_attachments(
        invitation.id, group.name, inviter_name, status="pending",
    )

    # 发 DM（跳过好友校验）
    dm_msg = await send_dm_message(
        invitation_repo.session,
        session_id=dm_session["session_id"],
        sender_id=invitation.inviter_id,
        content=content,
        attachments=attachments,
        message_type="group_invitation",
        skip_friendship_check=True,
    )

    # 回写 DM 关联信息到邀请记录
    invitation.dm_session_id = dm_session["session_id"]
    invitation.dm_message_id = dm_msg["id"]
    await invitation_repo.flush()

    # WebSocket 推送给双方——让 DM 列表实时更新
    try:
        from app.routers.ws import manager
        ws_msg = {**dm_msg, "conversation_type": "dm", "session_id": dm_session["session_id"]}
        await manager.send_to_user(invitation.invitee_id, {"type": "message", "data": ws_msg})
        await manager.send_to_user(invitation.inviter_id, {"type": "message", "data": ws_msg})
    except Exception as e:
        logger.warning(f"  ⚠️ WebSocket 推送邀请卡片失败（非致命）: {e}")

    logger.info(
        f"📨 群邀请 #{invitation.id}: {inviter_name} → user#{invitation.invitee_id} "
        f"加入群「{group.name}」"
    )
    return {"dm_message_id": dm_msg["id"], "dm_session_id": dm_session["session_id"]}


async def send_group_invitation(
    invitation_repo: InvitationRepository,
    group_id: int,
    inviter_id: int,
    invitee_id: int,
    message: str | None = None,
) -> dict:
    """发送群邀请：建记录，免审就立刻发卡片 DM，需审批就只留记录。

    Args:
        invitation_repo: 群邀请数据访问仓库（不再直接依赖 AsyncSession）。
            跨模块辅助调用（发 DM 卡片）通过 invitation_repo.session 桥接。

    Returns:
        dict with invitation_id、pending_approval，以及发卡片时的 dm_message_id/dm_session_id。
        pending_approval=True 时调用方不能回「邀请已发送」——对方还没收到任何东西。
    """
    invitation = await create_group_invitation(
        invitation_repo, group_id, inviter_id, invitee_id, message,
    )
    if invitation.approval_status == "pending":
        return {
            "invitation_id": invitation.id,
            "pending_approval": True,
            "dm_message_id": None,
            "dm_session_id": None,
        }
    return {
        "invitation_id": invitation.id,
        "pending_approval": False,
        **(await notify_invitee(invitation_repo, invitation)),
    }


async def approve_group_invitation(
    invitation_repo: InvitationRepository,
    invitation_id: int,
    approver_id: int,
) -> dict:
    """群主/管理员批准成员邀请：批准后才真正通知被邀请人。"""
    invitation = await _load_pending_approval(invitation_repo, invitation_id, approver_id)

    invitation.approval_status = "approved"
    invitation.approved_by = approver_id
    invitation.approved_at = _now()
    # 先落审批结果再发卡片：卡片发失败也不该让这条邀请回到「待审批」
    await invitation_repo.flush()

    dm = await notify_invitee(invitation_repo, invitation)
    await _notify_inviter(invitation_repo, invitation, GROUP_INVITE_APPROVED)
    await push_requests_changed(
        invitation_repo.session, invitation.group_id, exclude_user_id=approver_id,
    )
    logger.info(f"✅ 群邀请 #{invitation.id} 获批（审批人 user#{approver_id}），已通知被邀请人")
    return {
        "invitation_id": invitation.id,
        "group_id": invitation.group_id,
        "status": "approved",
        **dm,
    }


async def deny_group_invitation(
    invitation_repo: InvitationRepository,
    invitation_id: int,
    approver_id: int,
) -> dict:
    """群主/管理员驳回成员邀请：直接终态，被邀请人一无所知。

    status 也置为 rejected——否则这条记录会一直占着「该用户已有待处理邀请」的防重位，
    被同一个人重新邀请都邀请不了。
    """
    invitation = await _load_pending_approval(invitation_repo, invitation_id, approver_id)

    invitation.approval_status = "rejected"
    invitation.approved_by = approver_id
    invitation.approved_at = _now()
    invitation.status = "rejected"
    invitation.resolved_at = _now()
    await invitation_repo.flush()

    await _notify_inviter(invitation_repo, invitation, GROUP_INVITE_DENIED)
    await push_requests_changed(
        invitation_repo.session, invitation.group_id, exclude_user_id=approver_id,
    )
    logger.info(f"🚫 群邀请 #{invitation.id} 被驳回（审批人 user#{approver_id}）")
    return {
        "invitation_id": invitation.id,
        "group_id": invitation.group_id,
        "status": "rejected",
    }


async def accept_invitation(
    invitation_repo: InvitationRepository,
    invitation_id: int,
    user_id: int,
) -> dict:
    """接受群邀请：校验 → 入群 → 更新状态 → 更新卡片。

    Returns:
        dict with the updated invitation info
    """
    from app.chat.gm import add_member

    invitation = await invitation_repo.get(GroupInvitation, invitation_id)
    if invitation is None:
        raise ValueError("邀请不存在")
    if invitation.invitee_id != user_id:
        raise ValueError("这不是发给你的邀请")
    if not validate_invitation_transition(invitation.status, "accepted"):
        raise ValueError(f"邀请状态为 {invitation.status}，无法接受")

    # 入群
    await add_member(invitation_repo.session, invitation.group_id, "human", user_id)

    # 更新邀请状态
    invitation.status = "accepted"
    invitation.resolved_at = _now()

    # 更新 DM 卡片（attachments 中 status → accepted）
    await _update_dm_card(invitation_repo, invitation, "accepted")

    # 获取群名用于日志
    from app.models.group import Group
    group = await invitation_repo.get(Group, invitation.group_id)
    group_name = getattr(group, 'name', f"#{invitation.group_id}")

    await _notify_inviter(invitation_repo, invitation, GROUP_INVITE_ACCEPTED)
    logger.info(f"✅ 群邀请 #{invitation_id}: user#{user_id} 接受了「{group_name}」的邀请")

    return {
        "invitation_id": invitation.id,
        "group_id": invitation.group_id,
        "group_name": group_name,
        "status": "accepted",
    }


async def reject_invitation(
    invitation_repo: InvitationRepository,
    invitation_id: int,
    user_id: int,
) -> dict:
    """拒绝群邀请：校验 → 更新状态 → 更新卡片。
    不入群，仅更新卡片。
    """
    invitation = await invitation_repo.get(GroupInvitation, invitation_id)
    if invitation is None:
        raise ValueError("邀请不存在")
    if invitation.invitee_id != user_id:
        raise ValueError("这不是发给你的邀请")
    if not validate_invitation_transition(invitation.status, "rejected"):
        raise ValueError(f"邀请状态为 {invitation.status}，无法拒绝")

    invitation.status = "rejected"
    invitation.resolved_at = _now()

    await _update_dm_card(invitation_repo, invitation, "rejected")

    from app.models.group import Group
    group = await invitation_repo.get(Group, invitation.group_id)
    group_name = getattr(group, 'name', f"#{invitation.group_id}")

    await _notify_inviter(invitation_repo, invitation, GROUP_INVITE_DECLINED)
    logger.info(f"❌ 群邀请 #{invitation_id}: user#{user_id} 拒绝了「{group_name}」的邀请")

    return {
        "invitation_id": invitation.id,
        "group_id": invitation.group_id,
        "group_name": group_name,
        "status": "rejected",
    }


async def list_pending_invitations(
    invitation_repo: InvitationRepository,
    user_id: int,
) -> list[dict]:
    """列出用户的所有待处理邀请。

    只算已获批的：还需群审批的邀请在批准前不该出现在被邀请人这里（卡片也还没发）。
    """
    result = await invitation_repo.execute(
        select(GroupInvitation).where(
            GroupInvitation.invitee_id == user_id,
            GroupInvitation.status == "pending",
            GroupInvitation.approval_status == "approved",
        ).order_by(GroupInvitation.created_at.desc())
    )
    invitations = result.scalars().all()

    infos = []
    for inv in invitations:
        # 查群名
        from app.models.group import Group
        group = await invitation_repo.get(Group, inv.group_id)
        group_name = getattr(group, 'name', f"群#{inv.group_id}") if group else f"群#{inv.group_id}"

        inviter_name = await _username(invitation_repo, inv.inviter_id)
        invitee_name = await _username(invitation_repo, inv.invitee_id)

        infos.append(format_invitation_for_api(inv, group_name, inviter_name, invitee_name))

    return infos


async def list_pending_approval_invitations(
    db: AsyncSession,
    group_ids: list[int],
) -> list[GroupInvitation]:
    """待群主/管理员审批的成员邀请（「申请列表」聚合用；空 group_ids 直接返回空表）。"""
    if not group_ids:
        return []
    result = await db.execute(
        select(GroupInvitation).where(
            GroupInvitation.status == "pending",
            GroupInvitation.approval_status == "pending",
            GroupInvitation.group_id.in_(group_ids),
        ).order_by(GroupInvitation.created_at.desc())
    )
    return list(result.scalars().all())


# ═══════════════════════════════════════════════════════════════
# 内部辅助
# ═══════════════════════════════════════════════════════════════

async def _username(invitation_repo: InvitationRepository, user_id: int) -> str:
    """用户名兜底口径：查不到给「用户{id}」，别让卡片/列表出现 None"""
    row = (await invitation_repo.execute(
        select(User.username).where(User.id == user_id)
    )).one_or_none()
    return row[0] if row else f"用户{user_id}"


async def _notify_inviter(
    invitation_repo: InvitationRepository,
    invitation: GroupInvitation,
    kind: str,
) -> None:
    """邀请状态有变化就告诉邀请人。

    被邀请人那边有 DM 卡片，邀请人这边什么都没有——他只会看到"我邀请了人，然后没动静"。
    """
    from app.models.group import Group

    group = await invitation_repo.get(Group, invitation.group_id)
    await notify_user(
        invitation.inviter_id,
        kind,
        group_id=invitation.group_id,
        group_name=getattr(group, "name", None),
        target_id=invitation.invitee_id,
        target_name=await _username(invitation_repo, invitation.invitee_id),
    )


async def _load_pending_approval(
    invitation_repo: InvitationRepository,
    invitation_id: int,
    approver_id: int,
) -> GroupInvitation:
    """取一条待审批的邀请并校验审批权——approve/deny 共用的前置检查入口。"""
    from app.chat.gm import require_group_approver

    invitation = await invitation_repo.get(GroupInvitation, invitation_id)
    if invitation is None:
        raise ValueError("邀请不存在")
    if invitation.status != "pending":
        raise ValueError("该邀请已失效")
    if invitation.approval_status != "pending":
        raise ValueError("该邀请无需审批")
    await require_group_approver(invitation_repo.session, invitation.group_id, approver_id)
    return invitation


async def _update_dm_card(
    invitation_repo: InvitationRepository,
    invitation: GroupInvitation,
    new_status: str,
):
    """更新 DM 卡片消息的 attachments，反映新状态。
    前端收到 WebSocket 消息更新后重新渲染卡片。
    """
    if not invitation.dm_message_id:
        return

    from app.models.dm import DMMessage
    from app.models.group import Group

    dm_msg = await invitation_repo.get(DMMessage, invitation.dm_message_id)
    if dm_msg is None:
        return

    group = await invitation_repo.get(Group, invitation.group_id)
    group_name = getattr(group, 'name', f"群#{invitation.group_id}") if group else f"群#{invitation.group_id}"

    inviter_name = await _username(invitation_repo, invitation.inviter_id)

    new_attachments = build_invitation_attachments(
        invitation.id, group_name, inviter_name, status=new_status,
    )
    dm_msg.attachments = json.dumps(new_attachments)

    logger.info(f"  📝 DM 卡片 #{dm_msg.id} 状态更新: {invitation.status} → {new_status}")
