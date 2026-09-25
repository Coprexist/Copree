"""
群聊消息路由

与 routers/dm.py 对称：
    GET/POST /gm/{group_id}/messages  ↔  GET/POST /dm/{session_id}/messages

群管理（成员/公告/头像/邀请等）见 routers/groups.py。
"""
import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.gm import get_gm_messages, gm_message_to_dict, send_gm_message
from app.database import get_db
from app.routers.deps import require_group_member
from app.schemas.message import MessageResponse
from app.utils.auth import get_current_user

router = APIRouter(tags=["群聊消息"])


@router.get("/gm/{group_id}/messages", response_model=list[MessageResponse])
async def get_gm_message_list(
    group_id: int,
    limit: int = Query(20, ge=1, le=200),
    before_id: int | None = Query(None),
    after_id: int | None = Query(None),
    current_user: dict = Depends(require_group_member),
    db: AsyncSession = Depends(get_db),
):
    """获取群聊消息历史（游标分页，仅群成员）"""
    from app.models.user import User
    from app.models.agent import Agent

    messages = await get_gm_messages(db, group_id, limit, before_id=before_id, after_id=after_id)

    # 统一查所有 sender（可能是 users.id 或 agent.id，迁移后数据混合）
    all_ids = {m.sender_id for m in messages}
    name_map: dict[int, str] = {}
    avatar_map: dict[int, str] = {}
    state_map: dict[int, str] = {}

    if all_ids:
        # 先查 users 表
        u_result = await db.execute(select(User.id, User.username, User.type, User.avatar_url).where(User.id.in_(all_ids)))
        for row in u_result.all():
            uid, uname, utype, uavatar = row[0], row[1], row[2], row[3]
            name_map[uid] = uname
            avatar_map[uid] = uavatar or ''
            if utype == "ai":
                a = (await db.execute(select(Agent.name, Agent.avatar_url, Agent.state).where(Agent.user_id == uid))).first()
                if a:
                    name_map[uid] = a[0]
                    avatar_map[uid] = a[1] or uavatar or ''
                    state_map[uid] = a[2]

        # 群助手（独立实体，sender_id 为负值 = -group_assistant.id，产品 2026-08-13 定）
        ga_ids = {-sid for sid in all_ids if sid < 0}
        if ga_ids:
            from app.models.world import GroupAssistant
            ga_result = await db.execute(select(GroupAssistant.id, GroupAssistant.name).where(GroupAssistant.id.in_(ga_ids)))
            for gid, gname in ga_result.all():
                name_map[-gid] = gname
                avatar_map[-gid] = ''

    return [
        gm_message_to_dict(m, sender_name=name_map.get(m.sender_id), sender_avatar_url=avatar_map.get(m.sender_id), sender_state=state_map.get(m.sender_id))
        for m in messages
    ]


@router.post("/gm/{group_id}/messages", status_code=status.HTTP_201_CREATED)
async def send_gm(
    group_id: int,
    body: dict,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """发送群聊消息（含附件）"""
    from app.models.user import User as UserModel

    content = body.get("content", "")
    attachments = body.get("attachments")
    reply_to = body.get("reply_to")

    if not content.strip() and not attachments:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="消息内容或附件不能都为空")

    try:
        message = await send_gm_message(
            db, group_id=group_id, sender_type="human",
            sender_id=current_user["user_id"], content=content,
            reply_to=reply_to, attachments=attachments,
        )
        await db.flush()
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    # 取发送者头像
    sender_avatar = None
    u_result = await db.execute(select(UserModel).where(UserModel.id == current_user["user_id"]))
    u = u_result.scalar_one_or_none()
    if u:
        sender_avatar = u.avatar_url

    msg_data = gm_message_to_dict(message, sender_name=current_user["username"], sender_avatar_url=sender_avatar)

    # WebSocket 广播（外部通道也用同一个入口，别让两条链路各写一遍）
    from app.chat.group_delivery import broadcast_group_message

    await broadcast_group_message(group_id, msg_data)

    # 触发 AI 回复
    try:
        from app.ai.response_worker import message_queue
        message_queue.put_nowait({
            "conversation_type": "group",
            "group_id": group_id,
            "message_id": message.id,
            "content": content,
            "sender_type": "human",
            "sender_id": current_user["user_id"],
            "chain_depth": 0,
        })
    except asyncio.QueueFull:
        pass

    return msg_data
