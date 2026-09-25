"""
群邀请路由

两类角色、两套动作，别混：
- 被邀请人：accept（接受入群）/ reject（拒绝邀请），改的是邀请的 status；
- 群主/管理员审批：approve（批准，被邀请人才会收到卡片）/ deny（驳回），改的是 approval_status。
"""
from fastapi import APIRouter, Depends, HTTPException, status

from app.repositories.invitation_repo import InvitationRepository
from app.routers.deps import get_invitation_repo
from app.utils.auth import get_current_user

router = APIRouter(tags=["群邀请"])


@router.get("/group-invitations")
async def list_invitations(
    current_user: dict = Depends(get_current_user),
    invitation_repo: InvitationRepository = Depends(get_invitation_repo),
):
    """获取当前用户的待处理群邀请"""
    from app.services.social.invitation_service import list_pending_invitations
    invitations = await list_pending_invitations(invitation_repo, current_user["user_id"])
    return {"invitations": invitations}


@router.post("/group-invitations/{invitation_id}/accept")
async def accept_invitation(
    invitation_id: int,
    current_user: dict = Depends(get_current_user),
    invitation_repo: InvitationRepository = Depends(get_invitation_repo),
):
    """接受群邀请"""
    from app.services.social.invitation_service import accept_invitation as accept_inv
    try:
        result = await accept_inv(invitation_repo, invitation_id, current_user["user_id"])
        return result
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/group-invitations/{invitation_id}/reject")
async def reject_invitation(
    invitation_id: int,
    current_user: dict = Depends(get_current_user),
    invitation_repo: InvitationRepository = Depends(get_invitation_repo),
):
    """拒绝群邀请（被邀请人动作）"""
    from app.services.social.invitation_service import reject_invitation as reject_inv
    try:
        result = await reject_inv(invitation_repo, invitation_id, current_user["user_id"])
        return result
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/group-invitations/{invitation_id}/approve")
async def approve_invitation(
    invitation_id: int,
    current_user: dict = Depends(get_current_user),
    invitation_repo: InvitationRepository = Depends(get_invitation_repo),
):
    """批准成员邀请（仅该群群主/管理员）。批准后才通知被邀请人。"""
    from app.services.social.invitation_service import approve_group_invitation
    try:
        return await approve_group_invitation(
            invitation_repo, invitation_id, current_user["user_id"],
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/group-invitations/{invitation_id}/deny")
async def deny_invitation(
    invitation_id: int,
    current_user: dict = Depends(get_current_user),
    invitation_repo: InvitationRepository = Depends(get_invitation_repo),
):
    """驳回成员邀请（仅该群群主/管理员）。被邀请人不会收到任何通知。"""
    from app.services.social.invitation_service import deny_group_invitation
    try:
        return await deny_group_invitation(
            invitation_repo, invitation_id, current_user["user_id"],
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
