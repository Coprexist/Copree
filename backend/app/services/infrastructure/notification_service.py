"""站内通知（弹窗）的唯一出口

后端只说事实：kind + 群/会话 id + 名字。中文/英文/日文文案由前端 i18n 渲染——
同一条通知在后端拼三份字符串，改文案时必然漏掉一份。

常驻通知连接由 ws 层登记（见 routers/ws.py 的 notifications_subscribe）；
没人连着时 send_notification 静默丢弃——消息本体早已落库，弹窗只是提醒。
"""
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.gm import APPROVER_ROLES
from app.models.group import GroupMember

logger = logging.getLogger(__name__)

# 通知类目：前端 i18n 按这些 key 出文案，别在调用点写裸字符串
GROUP_JOIN_REQUESTED = "group_join_requested"    # → 审批人：有人申请入群
GROUP_JOIN_APPROVED = "group_join_approved"      # → 申请人：入群申请通过
GROUP_JOIN_REJECTED = "group_join_rejected"      # → 申请人：入群申请被拒
GROUP_INVITE_APPROVED = "group_invite_approved"  # → 邀请人：邀请获批（已通知被邀请人）
GROUP_INVITE_DENIED = "group_invite_denied"      # → 邀请人：邀请被驳回
GROUP_INVITE_ACCEPTED = "group_invite_accepted"  # → 邀请人：对方接受邀请进群了
GROUP_INVITE_DECLINED = "group_invite_declined"  # → 邀请人：对方拒绝了邀请


async def approver_ids(db: AsyncSession, group_id: int) -> list[int]:
    """该群的审批人（群主/管理员）。角色口径与审批权同一处，不另写一份。"""
    rows = await db.execute(
        select(GroupMember.member_id).where(
            GroupMember.group_id == group_id,
            GroupMember.member_type == "human",
            GroupMember.role.in_(APPROVER_ROLES),
        )
    )
    return [row[0] for row in rows.all()]


async def notify_user(user_id: int, kind: str, **fields) -> None:
    """给一个用户推一条站内通知（kind 决定前端文案模板）"""
    from app.routers.ws import manager
    try:
        await manager.send_notification(
            user_id, {"type": "push", "data": {"kind": kind, **fields}},
        )
    except Exception as e:
        logger.warning(f"站内通知投递失败（user#{user_id} / {kind}）：{e}")


async def notify_group_approvers(
    db: AsyncSession, group_id: int, kind: str, **fields,
) -> None:
    """给该群所有群主/管理员推通知（有人申请入群时用）"""
    payload = {"group_id": group_id, **fields}
    for approver_id in await approver_ids(db, group_id):
        await notify_user(approver_id, kind, **payload)


async def push_requests_changed(
    db: AsyncSession, group_id: int, exclude_user_id: int | None = None,
) -> None:
    """告诉审批人"申请列表变了"，让红点与列表立刻刷新（这不是弹窗）"""
    from app.routers.ws import manager
    for approver_id in await approver_ids(db, group_id):
        if approver_id == exclude_user_id:
            continue
        try:
            await manager.send_notification(approver_id, {"type": "requests_changed"})
        except Exception as e:
            logger.warning(f"申请列表刷新推送失败（user#{approver_id}）：{e}")
