"""
群聊与消息路由
"""
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status, Query, UploadFile, File
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import settings
from app.database import get_db
from app.schemas.group import (
    GroupCreateRequest, GroupInviteRequest, GroupJoinRequestCreate, GroupResponse,
    GroupUpdateRequest, AnnouncementRequest, RoleChangeRequest,
    SetDndRequest, UnreadSummaryItem, UnreadSummaryResponse, UnreadResponse,
    FederationShareRequest, GroupFederationStatus,
)
from app.chat.gm import (
    create_group,
    get_group,
    list_user_groups,
    add_member,
    get_group_members,
    update_group_settings,
    set_announcement,
    delete_announcement,
    change_member_role,
    remove_member,
    leave_group,
    disband_group,
    get_unread_info,
    update_last_read,
)
from app.chat.delivery import (
    set_group_dnd,
    cancel_group_dnd,
    is_member_in_dnd,
)
from app.utils.auth import get_current_user
from app.repositories.invitation_repo import InvitationRepository
from app.repositories.export_repo import ExportRepository
from app.routers.deps import get_invitation_repo
from app.routers.deps import get_export_repo
from app.routers.deps import require_group_member

router = APIRouter(tags=["群聊"])


@router.post("/groups", response_model=GroupResponse, status_code=status.HTTP_201_CREATED)
async def create_new_group(
    req: GroupCreateRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    invitation_repo: InvitationRepository = Depends(get_invitation_repo),
):
    """创建群聊"""
    # 必须至少选 1 个成员
    if not req.initial_members or len(req.initial_members) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="请至少选择一位群成员",
        )

    # 拆分为 AI（直接入群）和 human（走邀请）
    ai_members = [m for m in req.initial_members if m.get("type") == "ai"]
    human_members = [m for m in req.initial_members if m.get("type") == "human"]

    try:
        # 创建群聊（仅 AI 初始成员直接入群，人类后面发邀请）
        group = await create_group(
            db,
            name=req.name,
            owner_type="human",
            owner_id=current_user["user_id"],
            initial_members=ai_members,
        )

        # 人类成员：发送邀请
        from app.services.social.invitation_service import send_group_invitation
        invitations_sent = 0
        for hm in human_members:
            try:
                await send_group_invitation(
                    invitation_repo, group.id, current_user["user_id"], hm["id"],
                )
                invitations_sent += 1
            except ValueError:
                pass  # 已有待处理邀请，跳过

        return {
            "id": group.id,
            "name": group.name,
            "owner_type": group.owner_type,
            "owner_id": group.owner_id,
            "is_vector_accelerated": group.is_vector_accelerated,
            "is_paused": group.is_paused,
            "created_at": str(group.created_at) if group.created_at else None,
            "invitations_sent": invitations_sent,
        }
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/groups", response_model=list[GroupResponse])
async def list_my_groups(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取我的群聊列表（含置顶信息）"""
    from app.models.user_preferences import UserGroupPreference
    from sqlalchemy import select

    groups = await list_user_groups(db, current_user["user_id"])

    # 查当前用户的置顶偏好
    pref_result = await db.execute(
        select(UserGroupPreference).where(
            UserGroupPreference.user_id == current_user["user_id"]
        )
    )
    prefs = {p.group_id: p.is_pinned for p in pref_result.scalars().all()}

    for g in groups:
        g["is_pinned"] = prefs.get(g["id"], False)

    # 置顶优先，然后按最后消息时间降序
    def sort_key(g):
        t = 0
        if g.get("last_message_at"):
            try:
                t = datetime.fromisoformat(g["last_message_at"]).timestamp()
            except Exception:
                t = 0
        return (0 if g.get("is_pinned") else 1, -t)

    groups.sort(key=sort_key)
    return groups


@router.post("/groups/{group_id}/pin")
async def pin_group(
    group_id: int,
    body: dict,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """置顶/取消置顶群聊"""
    from app.models.user_preferences import UserGroupPreference
    from sqlalchemy import select
    result = await db.execute(select(UserGroupPreference).where(
        UserGroupPreference.user_id == current_user["user_id"],
        UserGroupPreference.group_id == group_id,
    ))
    pref = result.scalar_one_or_none()
    if pref:
        pref.is_pinned = body.get("is_pinned", True)
    else:
        pref = UserGroupPreference(
            user_id=current_user["user_id"],
            group_id=group_id,
            is_pinned=body.get("is_pinned", True),
        )
        db.add(pref)
    await db.commit()
    return {"is_pinned": pref.is_pinned}


@router.get("/groups/{group_id}", response_model=GroupResponse)
async def get_group_detail(
    group_id: int,
    current_user: dict = Depends(require_group_member),
    db: AsyncSession = Depends(get_db),
):
    """获取群聊详情（仅群成员）"""
    group = await get_group(db, group_id)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="群聊不存在")

    # 统计成员数量 & 在线人数
    from app.models.group import GroupMember
    from app.services.infrastructure.online_tracker import get_user_online_status
    member_result = await db.execute(
        select(GroupMember).where(GroupMember.group_id == group_id)
    )
    members = member_result.scalars().all()
    member_count = len(members)
    online_count = sum(1 for m in members if get_user_online_status(m.member_id))

    return {
        "id": group.id,
        "name": group.name,
        "owner_type": group.owner_type,
        "owner_id": group.owner_id,
        "is_paused": group.is_paused,
        "is_vector_accelerated": group.is_vector_accelerated,
        "avatar_mode": group.avatar_mode or "default",
        "avatar_url": group.avatar_url,
        "include_ai_in_avatar": group.include_ai_in_avatar,
        "searchable": bool(group.searchable),
        "auto_approve_join": bool(group.auto_approve_join),
        "approve_invites": bool(group.approve_invites),
        "created_at": str(group.created_at) if group.created_at else None,
        "member_count": member_count,
        "online_count": online_count,
    }


@router.post("/groups/{group_id}/invite")
async def invite_member(
    group_id: int,
    req: GroupInviteRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    invitation_repo: InvitationRepository = Depends(get_invitation_repo),
):
    """邀请成员加入群聊。AI 直接入群，人类发邀请卡片 DM。"""
    try:
        if req.member_type == "ai":
            member = await add_member(
                db,
                group_id=group_id,
                member_type=req.member_type,
                member_id=req.member_id,
            )
            return {"message": "已加入群聊", "group_id": group_id, "method": "direct"}
        else:
            from app.services.social.invitation_service import send_group_invitation
            result = await send_group_invitation(
                invitation_repo, group_id, current_user["user_id"], req.member_id, req.message,
            )
            pending = result.get("pending_approval", False)
            return {
                # 等审批时不能说「已发送」——对方此刻还没收到任何东西
                "message": "邀请已提交，等待群主或管理员审批" if pending else "邀请已发送",
                "group_id": group_id,
                "method": "invitation",
                **result,
            }
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


# ============================================================
# 发现与入群：搜索 → 申请 → 审批
# ============================================================


@router.post("/groups/{group_id}/join")
async def join_group(
    group_id: int,
    req: GroupJoinRequestCreate | None = None,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """申请加入群聊。群开着「加群需审批」时落申请等群主/管理员处理，否则直接入群。"""
    from app.services.social.group_join_service import request_join
    try:
        return await request_join(
            db, group_id, current_user["user_id"], req.message if req else None,
            actor_name=current_user.get("username"),
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


# 审批端点用 join-requests 前缀：{group_id} 是 int，路径段匹配不上会继续往下找，不会误命中
@router.post("/groups/join-requests/{request_id}/approve")
async def approve_join_request(
    request_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """通过入群申请（仅该群群主/管理员）"""
    from app.services.social.group_join_service import resolve_join_request
    try:
        return await resolve_join_request(db, request_id, current_user["user_id"], approve=True)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/groups/join-requests/{request_id}/reject")
async def reject_join_request(
    request_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """拒绝入群申请（仅该群群主/管理员）"""
    from app.services.social.group_join_service import resolve_join_request
    try:
        return await resolve_join_request(db, request_id, current_user["user_id"], approve=False)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/groups/{group_id}/members")
async def list_members(
    group_id: int,
    current_user: dict = Depends(require_group_member),
    db: AsyncSession = Depends(get_db),
):
    """获取群成员列表（含名称和在线状态，用于 @提及自动补全；仅群成员）"""
    from app.models.user import User
    from app.models.agent import Agent as AgentModel
    members = await get_group_members(db, group_id)
    result = []
    for m in members:
        name = None
        state = None
        if m.member_type == "human":
            u = await db.get(User, m.member_id)
            if u:
                name = u.username
            from app.services.infrastructure.online_tracker import get_user_online_status
            if get_user_online_status(m.member_id):
                state = "active"
        elif m.member_type == "ai":
            # v2.0.0: member_id 统一为 user_id，同时兼容旧 agent.id
            a_res = await db.execute(
                select(AgentModel).where(AgentModel.user_id == m.member_id)
            )
            a = a_res.scalar_one_or_none()
            if a is None:
                # 向后兼容：可能是尚未迁移的旧 agent.id
                a_res = await db.execute(
                    select(AgentModel).where(AgentModel.id == m.member_id)
                )
                a = a_res.scalar_one_or_none()
            if a:
                name = a.name
                state = a.state
        result.append({
            "type": m.member_type,
            "id": m.member_id,
            "name": name or f"{m.member_type}:{m.member_id}",
            "state": state,
            "role": m.role,
            "dnd_until": str(m.dnd_until) if m.dnd_until else None,
        })
    return result


@router.post("/groups/{group_id}/dnd")
async def set_dnd(
    group_id: int,
    req: SetDndRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    为当前用户（或其所拥有的 AI）在指定群聊设置免打扰。
    请求中的 group_id 会覆盖 body 中的 group_id。
    """
    try:
        actual_group_id = group_id
        # ⚠️ 必须显式传 member_type="human"，因为 set_group_dnd 默认是 "ai"
        #（向后兼容 AI worker/tool_registry）。如果漏传，human 用户查不到记录会报错。
        member = await set_group_dnd(
            db,
            agent_id=current_user["user_id"],
            group_id=actual_group_id,
            duration_minutes=req.duration_minutes,
            member_type="human",
        )
        return {
            "message": "免打扰已设置",
            "group_id": actual_group_id,
            "dnd_until": str(member.dnd_until) if member.dnd_until else "永久",
        }
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/groups/{group_id}/dnd/cancel")
async def cancel_dnd(
    group_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """取消指定群聊的免打扰"""
    try:
        await cancel_group_dnd(db, current_user["user_id"], group_id, member_type="human")
        return {"message": "免打扰已取消", "group_id": group_id}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/groups/{group_id}/dnd/status")
async def check_dnd(
    group_id: int,
    agent_id: int = Query(..., description="AI ID"),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """检查指定 AI 在群聊中是否处于免打扰状态"""
    in_dnd = await is_member_in_dnd(db, agent_id, group_id)
    return {"agent_id": agent_id, "group_id": group_id, "in_dnd": in_dnd}


# ============================================================
# Phase 4: 群聊治理端点
# ============================================================


@router.patch("/groups/{group_id}")
async def update_group(
    group_id: int,
    req: GroupUpdateRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """更新群聊设置（名称、公告、发言限制、头像等）。仅群主/管理员可操作。"""
    try:
        updates = req.model_dump(exclude_none=True)

        group = await update_group_settings(
            db, group_id, current_user["user_id"], updates,
        )
        return {
            "id": group.id,
            "name": group.name,
            "owner_type": group.owner_type,
            "is_paused": group.is_paused,
            "owner_id": group.owner_id,
            "is_vector_accelerated": group.is_vector_accelerated,
            "announcement": group.announcement,
            "speak_limit_per_minute": group.speak_limit_per_minute or 0,
            "speak_limit_window_seconds": group.speak_limit_window_seconds or 120,
            "avatar_mode": group.avatar_mode or "default",
            "avatar_url": group.avatar_url,
            "include_ai_in_avatar": group.include_ai_in_avatar,
            "searchable": bool(group.searchable),
            "auto_approve_join": bool(group.auto_approve_join),
            "approve_invites": bool(group.approve_invites),
            "created_at": str(group.created_at) if group.created_at else None,
        }
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/groups/{group_id}/avatar", status_code=status.HTTP_200_OK)
async def upload_group_avatar(
    group_id: int,
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """上传群聊自定义头像。仅群主/管理员可操作。"""
    from app.models.group import Group, GroupMember
    from sqlalchemy import select

    # 鉴权
    result = await db.execute(select(Group).where(Group.id == group_id))
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="群聊不存在")

    member = await db.execute(
        select(GroupMember).where(
            GroupMember.group_id == group_id,
            GroupMember.member_type == "human",
            GroupMember.member_id == current_user["user_id"],
        )
    )
    gm = member.scalar_one_or_none()
    if not gm or gm.role not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="仅群主或管理员可操作")

    # 读取并校验
    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(400, "头像文件大小不能超过 5MB")

    from app.utils.image_compress import compress_avatar
    content = compress_avatar(content)

    import os
    import uuid
    upload_dir = settings.avatars_dir
    os.makedirs(upload_dir, exist_ok=True)

    # 删除旧文件
    if group.avatar_url:
        old_name = group.avatar_url.rsplit('/', 1)[-1]
        for fname in (old_name, f"thumb_{old_name}"):
            old_path = os.path.join(upload_dir, fname)
            if os.path.isfile(old_path):
                os.remove(old_path)

    ext = os.path.splitext(file.filename or ".png")[1] or ".png"
    filename = f"group_{group_id}_{uuid.uuid4().hex[:8]}{ext}"
    filepath = os.path.join(upload_dir, filename)
    with open(filepath, "wb") as f:
        f.write(content)

    # 生成缩略图
    from app.utils.image_compress import make_avatar_thumbnail
    thumb = make_avatar_thumbnail(content)
    thumb_name = f"thumb_{filename}"
    with open(os.path.join(upload_dir, thumb_name), "wb") as f:
        f.write(thumb)

    avatar_url = f"/api/fs/download-avatar/{filename}"
    group.avatar_url = avatar_url
    group.avatar_mode = "custom"
    await db.flush()

    return {
        "avatar_url": avatar_url,
        "avatar_mode": "custom",
        "message": "头像已更新",
    }


@router.post("/groups/{group_id}/announcement")
async def create_announcement(
    group_id: int,
    req: AnnouncementRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """设置群公告。仅群主/管理员可操作。"""
    try:
        content = await set_announcement(
            db, group_id, req.content, current_user["user_id"],
        )
        # 广播公告到群聊（作为系统消息）
        from app.routers.ws import manager
        await manager.broadcast_to_group(
            group_id,
            {
                "type": "announcement",
                "data": {
                    "group_id": group_id,
                    "content": content[:200],
                    "operator": current_user["username"],
                },
            },
        )
        return {"message": "公告已更新", "content": content}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.delete("/groups/{group_id}/announcement")
async def remove_announcement(
    group_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """删除群公告。仅群主/管理员可操作。"""
    try:
        await delete_announcement(db, group_id, current_user["user_id"])
        return {"message": "公告已删除"}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/groups/{group_id}/toggle-pause")
async def toggle_pause(
    group_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """暂停/恢复群聊 AI 触发。仅群主/管理员可操作。"""
    try:
        from app.models.group import Group
        result = await db.execute(select(Group).where(Group.id == group_id))
        group = result.scalar_one_or_none()
        if group is None:
            raise HTTPException(status_code=404, detail="群聊不存在")
        is_owner = (group.owner_type == "human" and group.owner_id == current_user["user_id"])
        from app.models.group import GroupMember
        gm_result = await db.execute(
            select(GroupMember).where(
                GroupMember.group_id == group_id,
                GroupMember.member_type == "human",
                GroupMember.member_id == current_user["user_id"],
            )
        )
        gm = gm_result.scalar_one_or_none()
        is_admin = gm and gm.role in ("owner", "admin")
        if not is_owner and not is_admin:
            raise HTTPException(status_code=403, detail="仅群主/管理员可操作")
        group.is_paused = not group.is_paused
        await db.commit()
        return {"is_paused": group.is_paused, "message": "已暂停" if group.is_paused else "已恢复"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/groups/{group_id}/members/{member_type}/{member_id}/role")
async def update_member_role(
    group_id: int,
    member_type: str,
    member_id: int,
    req: RoleChangeRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """修改成员角色（提拔/降级）。仅群主可操作。"""
    try:
        member = await change_member_role(
            db, group_id, current_user["user_id"],
            member_type, member_id, req.role,
        )
        return {
            "message": f"角色已更新为 {req.role}",
            "member_type": member.member_type,
            "member_id": member.member_id,
            "role": member.role,
        }
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.delete("/groups/{group_id}/members/{member_type}/{member_id}")
async def kick_member(
    group_id: int,
    member_type: str,
    member_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """将成员踢出群聊。仅群主/管理员可操作。"""
    try:
        await remove_member(
            db, group_id, current_user["user_id"],
            member_type, member_id,
        )
        return {"message": "成员已移出群聊"}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/groups/{group_id}/leave")
async def leave(
    group_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """退出群聊。群主需先转让。"""
    try:
        await leave_group(db, group_id, "human", current_user["user_id"])
        return {"message": "已退出群聊"}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.delete("/groups/{group_id}")
async def delete_group(
    group_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """解散群聊（仅群主可操作）"""
    try:
        await disband_group(db, group_id, current_user["user_id"])
        return {"message": "群聊已解散", "group_id": group_id}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/groups/{group_id}/transfer-owner")
async def transfer_owner(
    group_id: int,
    body: dict,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """转让群主权限。仅当前群主可操作。"""
    from app.models.group import Group, GroupMember
    from sqlalchemy import select

    # 验证当前用户是群主
    result = await db.execute(select(Group).where(Group.id == group_id))
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(404, "群聊不存在")
    if not (group.owner_type == "human" and group.owner_id == current_user["user_id"]):
        raise HTTPException(403, "仅群主可转让")

    target_type = body.get("member_type", "human")
    target_id = body.get("member_id")
    if not target_id:
        raise HTTPException(400, "请指定新群主")

    # 验证目标成员存在
    mr = await db.execute(select(GroupMember).where(
        GroupMember.group_id == group_id,
        GroupMember.member_type == target_type,
        GroupMember.member_id == target_id,
    ))
    target = mr.scalar_one_or_none()
    if not target:
        raise HTTPException(404, "目标成员不在群中")

    # 转让：旧 owner → admin，目标 → owner
    old_mr = await db.execute(select(GroupMember).where(
        GroupMember.group_id == group_id,
        GroupMember.member_type == group.owner_type,
        GroupMember.member_id == group.owner_id,
    ))
    old = old_mr.scalar_one_or_none()
    if old:
        old.role = "admin"
    target.role = "owner"
    group.owner_id = target_id
    group.owner_type = target_type
    await db.flush()
    return {"message": "群主已转让", "new_owner": {"type": target_type, "id": target_id}}


@router.get("/groups/{group_id}/unread")
async def unread_info(
    group_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取当前用户在该群的未读信息。"""
    return await get_unread_info(db, group_id, current_user["user_id"])


@router.post("/groups/{group_id}/read")
async def mark_read(
    group_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """标记当前用户已读该群消息（进入群聊时调用）。"""
    updated = await update_last_read(db, group_id, "human", current_user["user_id"])
    # 成员记录缺失时尝试补创建（重建容器后 group_members 可能不完整）
    if not updated:
        try:
            db.add(GroupMember(
                group_id=group_id, member_type="human", member_id=current_user["user_id"],
                role="member", last_read_at=datetime.now(timezone.utc).replace(tzinfo=None),
            ))
            await db.flush()
            updated = True
        except Exception:
            pass
    return {"ok": True, "updated": updated}


# ---------- 联邦共享控制（v0.2.0: 群主/AI制作者按群控制） ----------

@router.get("/groups/{group_id}/federation/peers")
async def get_federation_peers(
    group_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    获取群联邦共享状态：列出所有对等端及其共享状态。
    需要群主/AI制作者权限。
    """
    from app.services.federation.federation_service import get_group_federation_peers
    result = await get_group_federation_peers(db, group_id, current_user["user_id"])
    if result.get("error"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=result["message"])
    return result


@router.post("/groups/{group_id}/federation/share")
async def share_group_federation(
    group_id: int,
    req: FederationShareRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    将群共享到指定对等端。
    需要群主/AI制作者权限。
    """
    from app.services.federation.federation_service import share_group_to_peers
    result = await share_group_to_peers(
        db, group_id, req.peer_ids, current_user["user_id"],
    )
    if result.get("error"):
        msg = result["message"]
        if "无权限" in msg:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=msg)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg)
    return result


@router.post("/groups/{group_id}/federation/unshare")
async def unshare_group_federation(
    group_id: int,
    req: FederationShareRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    取消群对指定对等端的联邦共享。
    需要群主/AI制作者权限。
    """
    from app.services.federation.federation_service import unshare_group_from_peers
    result = await unshare_group_from_peers(
        db, group_id, req.peer_ids, current_user["user_id"],
    )
    if result.get("error"):
        msg = result["message"]
        if "无权限" in msg:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=msg)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg)
    return result


# ---------- 聊天记录导出 ----------

@router.get("/groups/{group_id}/export")
async def export_chat(
    group_id: int,
    fmt: str = Query("json", pattern="^(json|txt|html)$"),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    export_repo: ExportRepository = Depends(get_export_repo),
):
    """导出群聊记录（json / txt / html）"""
    from app.services.content.export_service import export_chat_history

    # 校验群成员身份
    group = await get_group(db, group_id)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="群聊不存在")

    try:
        content, media_type, filename = await export_chat_history(
            export_repo, group_id, fmt, date_from, date_to
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@router.get("/groups/{group_id}/activity")
async def get_group_activity(
    group_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """获取群聊当前 AI 思考/输入中状态（用于进入对话时恢复活动指示器）"""
    from app.ai.response_worker import get_thinking_state
    conv_key = f"group:{group_id}"
    return get_thinking_state(conv_key)
