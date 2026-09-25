"""申请列表路由

把「好友申请 / 入群申请 / 待我审批的成员邀请」聚合成一个口径：侧边栏红点与
「申请列表」页共用它，前端不再各算各的（2026-09-21 用户要求：名字从"好友申请"
改成"申请列表"，入群申请与好友申请在同一处审批）。

审批权就是可见性：只有我是该群群主/管理员时，本群的入群申请与待审邀请才可见。
审批动作不在这里（写操作归各领域）：好友走 /friends/requests/*，
入群走 /groups/join-requests/*，邀请走 /group-invitations/{id}/approve|deny。
"""
import logging

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.gm import get_approver_group_ids
from app.database import get_db
from app.models.friendship import FriendshipRequest
from app.models.group import Group
from app.models.user import User
from app.services.social.group_join_service import list_pending_join_requests
from app.services.social.invitation_service import list_pending_approval_invitations
from app.utils.auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(tags=["申请列表"])


def build_request_item(
    kind: str,
    row_id: int,
    user_id: int,
    *,
    user_name: str | None = None,
    avatar_url: str | None = None,
    message: str | None = None,
    created_at=None,
    group_id: int | None = None,
    group_name: str | None = None,
    target_id: int | None = None,
    target_name: str | None = None,
) -> dict:
    """申请列表条目：三种申请共用一个字段口径，前端只按 kind 分区渲染。

    target_id/target_name 只有邀请审批用（谁邀请了谁），其余为 None。
    """
    return {
        "kind": kind,
        "id": row_id,
        "user_id": user_id,
        "user_name": user_name,
        "avatar_url": avatar_url,
        "message": message,
        "created_at": created_at,
        "group_id": group_id,
        "group_name": group_name,
        "target_id": target_id,
        "target_name": target_name,
    }


async def _user_profiles(db: AsyncSession, user_ids: set[int]) -> dict[int, dict]:
    """批量取用户名与头像。逐个 get 会 N+1——列表一次可能几十条。"""
    if not user_ids:
        return {}
    rows = await db.execute(
        select(User.id, User.username, User.avatar_url).where(User.id.in_(user_ids))
    )
    return {
        uid: {"name": name, "avatar_url": avatar}
        for uid, name, avatar in rows.all()
    }


def _display(profiles: dict[int, dict], user_id: int) -> tuple[str, str | None]:
    """用户名/头像兜底口径：查不到给「用户{id}」，别让列表出现 None"""
    profile = profiles.get(user_id) or {}
    return profile.get("name") or f"用户{user_id}", profile.get("avatar_url")


@router.get("/requests/pending")
async def list_pending_requests(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """待我处理的申请（统一列表；红点数量 = len(items)）。

    kind: friend=收到的好友申请 | group_join=待审入群申请 | group_invite=待审的成员邀请
    """
    user_id = int(current_user["user_id"])

    friend_rows = (await db.execute(
        select(FriendshipRequest).where(
            FriendshipRequest.status == "pending",
            FriendshipRequest.target_type == "human",
            FriendshipRequest.target_id == user_id,
        ).order_by(FriendshipRequest.created_at.desc())
    )).scalars().all()

    group_ids = await get_approver_group_ids(db, user_id)
    join_rows = await list_pending_join_requests(db, group_ids)
    invite_rows = await list_pending_approval_invitations(db, group_ids)

    group_names: dict[int, str] = {}
    if group_ids:
        group_names = {
            gid: name
            for gid, name in (await db.execute(
                select(Group.id, Group.name).where(Group.id.in_(group_ids))
            )).all()
        }

    profiles = await _user_profiles(
        db,
        {row.requester_id for row in friend_rows}
        | {row.user_id for row in join_rows}
        | {row.inviter_id for row in invite_rows}
        | {row.invitee_id for row in invite_rows},
    )

    items = []
    for row in friend_rows:
        name, avatar = _display(profiles, row.requester_id)
        items.append(build_request_item(
            "friend", row.id, row.requester_id,
            user_name=name, avatar_url=avatar,
            message=row.message, created_at=row.created_at,
        ))
    for row in join_rows:
        name, avatar = _display(profiles, row.user_id)
        items.append(build_request_item(
            "group_join", row.id, row.user_id,
            user_name=name, avatar_url=avatar,
            message=row.message, created_at=row.created_at,
            group_id=row.group_id, group_name=group_names.get(row.group_id),
        ))
    for row in invite_rows:
        inviter_name, _ = _display(profiles, row.inviter_id)
        invitee_name, invitee_avatar = _display(profiles, row.invitee_id)
        items.append(build_request_item(
            "group_invite", row.id, row.inviter_id,
            user_name=inviter_name, avatar_url=invitee_avatar,
            message=row.message, created_at=row.created_at,
            group_id=row.group_id, group_name=group_names.get(row.group_id),
            target_id=row.invitee_id, target_name=invitee_name,
        ))
    return items
