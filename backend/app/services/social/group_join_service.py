"""
入群申请服务

群开着「加群需审批」（groups.auto_approve_join=false）时，申请先落
group_join_requests，群主/管理员在「申请列表」里通过后才真正入群；
关着时直接入群——这是 2026-09-21 之前的行为，也是默认值。

两条路径都收在 request_join 里：调用方只调一次，不必自己读开关，
否则「忘了判断开关」这类 bug 会散到每个入群入口。
"""
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.gm import add_member, is_group_member, require_group_approver
from app.models.group import Group, GroupJoinRequest
from app.services.infrastructure.notification_service import (
    GROUP_JOIN_APPROVED, GROUP_JOIN_REJECTED, GROUP_JOIN_REQUESTED,
    notify_group_approvers, notify_user, push_requests_changed,
)

logger = logging.getLogger(__name__)

# 申请状态（与 models/group.py 的注释、库里的 CheckConstraint 口径一致）
PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"


def _now() -> datetime:
    """库里的时间列都是 naive UTC（历史口径），时间统一从这里取"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def list_pending_join_requests(
    db: AsyncSession, group_ids: list[int],
) -> list[GroupJoinRequest]:
    """待审入群申请（限这些群）。空 group_ids 直接返回空表，避免 IN () 语法错误。"""
    if not group_ids:
        return []
    rows = await db.execute(
        select(GroupJoinRequest).where(
            GroupJoinRequest.status == PENDING,
            GroupJoinRequest.group_id.in_(group_ids),
        ).order_by(GroupJoinRequest.created_at.desc())
    )
    return list(rows.scalars().all())


async def request_join(
    db: AsyncSession,
    group_id: int,
    user_id: int,
    message: str | None = None,
    actor_name: str | None = None,
) -> dict:
    """申请入群。返回字典里的 status 是 joined（直接进）或 pending（等审批）。

    actor_name 由路由从登录态带下来——为了一句弹窗文案多查一次用户名不值得。
    """
    group = await db.get(Group, group_id)
    if group is None:
        raise ValueError("群聊不存在")
    if await is_group_member(db, group_id, "human", user_id):
        raise ValueError("你已经在该群中")

    if group.auto_approve_join:
        await add_member(db, group_id, "human", user_id)
        logger.info(f"🚪 user#{user_id} 直接加入群「{group.name}」#{group_id}（该群未开启审批）")
        return {
            "status": "joined",
            "group_id": group_id,
            "group_name": group.name,
            "auto_approved": True,
        }

    existing = (await db.execute(
        select(GroupJoinRequest).where(
            GroupJoinRequest.group_id == group_id,
            GroupJoinRequest.user_id == user_id,
            GroupJoinRequest.status == PENDING,
        )
    )).scalar_one_or_none()
    if existing:
        raise ValueError("你已提交过申请，请等待群主或管理员处理")

    request = GroupJoinRequest(
        group_id=group_id, user_id=user_id, status=PENDING, message=message,
    )
    db.add(request)
    await db.flush()
    await db.refresh(request)

    # 通知审批人：弹窗 + 让他们的申请列表/红点立刻刷新（不然要等下一个轮询周期）
    await notify_group_approvers(
        db, group_id, GROUP_JOIN_REQUESTED,
        actor_id=user_id, actor_name=actor_name, group_name=group.name,
    )
    await push_requests_changed(db, group_id, exclude_user_id=user_id)

    logger.info(f"📝 user#{user_id} 申请加入群「{group.name}」#{group_id}，等待审批")
    return {
        "status": "pending",
        "request_id": request.id,
        "group_id": group_id,
        "group_name": group.name,
        "auto_approved": False,
    }


async def resolve_join_request(
    db: AsyncSession, request_id: int, approver_id: int, approve: bool,
) -> dict:
    """群主/管理员审批入群申请；通过才真正入群。"""
    request = await db.get(GroupJoinRequest, request_id)
    if request is None:
        raise ValueError("申请不存在")
    if request.status != PENDING:
        raise ValueError("该申请已处理")
    await require_group_approver(db, request.group_id, approver_id)

    group = await db.get(Group, request.group_id)
    group_name = group.name if group else f"群#{request.group_id}"

    request.status = APPROVED if approve else REJECTED
    request.resolver_id = approver_id
    request.resolved_at = _now()
    if approve:
        await add_member(db, request.group_id, "human", request.user_id)
    await db.flush()

    # 结果通知申请人自己；其他审批人那边同步移除这条待办
    await notify_user(
        request.user_id,
        GROUP_JOIN_APPROVED if approve else GROUP_JOIN_REJECTED,
        group_id=request.group_id, group_name=group_name,
    )
    await push_requests_changed(db, request.group_id, exclude_user_id=approver_id)

    logger.info(
        f"{'✅' if approve else '❌'} 入群申请 #{request_id}: user#{approver_id} "
        f"{'通过' if approve else '拒绝'} user#{request.user_id} 加入「{group_name}」"
    )
    return {
        "request_id": request.id,
        "group_id": request.group_id,
        "group_name": group_name,
        "user_id": request.user_id,
        "status": request.status,
    }
