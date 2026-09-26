"""
好友系统路由
搜索、好友申请、好友列表管理
"""
import logging
from fastapi import APIRouter, Depends, HTTPException, status, Query
from app.repositories.friend_repo import FriendRepository
from app.schemas.friendship import (
    FriendRequestCreate, FriendRequestResponse,
    FriendResponse, SearchResponse, SearchResult,
)
from app.services.social.friend_service import (
    send_friend_request, accept_friend_request, reject_friend_request,
    remove_friend, list_friends, list_friend_requests, search_entities,
    trigger_ai_auto_respond,
)
from app.utils.auth import get_current_user
from app.routers.deps import get_friend_repo
from app.routers.ws import manager

logger = logging.getLogger(__name__)
router = APIRouter(tags=["好友"])


async def _notify_friend_request(event_type: str, data: dict, target_user_id: int):
    """通过 WebSocket 向目标用户推送好友相关通知"""
    try:
        await manager.send_to_user(target_user_id, {
            "type": "friend_notification",
            "data": {
                "event": event_type,
                **data,
            },
        })
    except Exception as e:
        logger.warning(f"推送好友通知给用户 {target_user_id} 失败: {e}")


@router.get("/search", response_model=SearchResponse)
async def search(
    q: str = Query(..., min_length=1, description="搜索关键词"),
    current_user: dict = Depends(get_current_user),
    friend_repo: FriendRepository = Depends(get_friend_repo),
):
    """搜索用户和 AI（支持按用户名/AI名搜索）"""
    results = await search_entities(
        friend_repo=friend_repo,
        query=q,
        current_user_id=current_user["user_id"],
    )
    return {"results": results, "query": q}


@router.get("/friends", response_model=list[FriendResponse])
async def list_my_friends(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current_user: dict = Depends(get_current_user),
    friend_repo: FriendRepository = Depends(get_friend_repo),
):
    """获取我的好友列表"""
    friends = await list_friends(
        friend_repo=friend_repo,
        user_id=current_user["user_id"],
        limit=limit,
        offset=offset,
    )
    # 注入在线状态（统一函数）
    from app.services.infrastructure.online_tracker import get_user_online_status
    for f in friends:
        friend_uid = f.get("friend_user_id")
        if friend_uid and f["friend_type"] == "human" and get_user_online_status(friend_uid):
            f["state"] = "active"
    return friends


@router.post("/friends/{friendship_id}/toggle-priority")
async def toggle_friend_priority(
    friendship_id: int,
    current_user: dict = Depends(get_current_user),
    friend_repo: FriendRepository = Depends(get_friend_repo),
):
    """切换特别关心状态"""
    friendship = await friend_repo.get_friendship_by_id(friendship_id, current_user["user_id"])
    if friendship is None:
        raise HTTPException(status_code=404, detail="好友关系不存在")
    friendship.is_priority = not friendship.is_priority
    await friend_repo.flush()
    return {"is_priority": friendship.is_priority}


@router.post("/friends/requests", status_code=status.HTTP_201_CREATED)
async def create_friend_request(
    req: FriendRequestCreate,
    current_user: dict = Depends(get_current_user),
    friend_repo: FriendRepository = Depends(get_friend_repo),
):
    """发送好友申请"""
    try:
        result = await send_friend_request(
            friend_repo=friend_repo,
            requester_id=current_user["user_id"],
            target_type=req.target_type,
            target_id=req.target_id,
            message=req.message,
        )
        # target_id 统一为 users.id，直接使用
        target_uid = req.target_id

        # WebSocket 通知目标用户
        await _notify_friend_request("request_received", {
            "request_id": result.get("request_id"),
            "requester_id": current_user["user_id"],
            "requester_name": current_user.get("username", ""),
            "target_type": req.target_type,
            "target_id": req.target_id,
            "message": req.message,
            "status": result.get("status", "pending"),
            "auto_respond": result.get("auto_respond", False),
        }, target_uid)

        # 双向自动接受：只发双方附言，不需要通知
        if result.get("auto") and target_uid:
            if req.message:
                await _inject_friend_greeting(
                    friend_repo, current_user["user_id"], target_uid,
                    req.message, prefix="",
                )

        # 🎯 目标 AI 开启「自动响应好友申请」→ 触发 AI 自主处理（不建 DM 会话，通不通过由 AI 判断）
        if req.target_type == "ai" and result.get("auto_respond"):
            try:
                target_agent = await friend_repo.get_agent_by_user_id(req.target_id)
                if target_agent:
                    await trigger_ai_auto_respond(
                        friend_repo=friend_repo,
                        agent=target_agent,
                        requester_id=current_user["user_id"],
                        requester_name=current_user.get("username") or f"用户{current_user['user_id']}",
                        message=req.message or "",
                    )
                    logger.info(f"🎯 AI「{target_agent.name}」自动响应好友申请（来自 {current_user.get('username')}）")
            except Exception as e:
                logger.warning(f"AI 自动响应好友申请失败: {e}")
            if result.get("reverse_message"):
                await _inject_friend_greeting(
                    friend_repo, target_uid, current_user["user_id"],
                    result["reverse_message"], prefix="",
                )

        return result
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/friends/requests", response_model=list[FriendRequestResponse])
async def list_requests(
    status_filter: str = Query("pending", description="pending | accepted | rejected"),
    received_only: bool = Query(False, description="仅返回收到的申请（用于红点计数）"),
    current_user: dict = Depends(get_current_user),
    friend_repo: FriendRepository = Depends(get_friend_repo),
):
    """获取我的好友申请（默认收到 + 发出，received_only 仅返回收到的）"""
    return await list_friend_requests(
        friend_repo=friend_repo,
        user_id=current_user["user_id"],
        status=status_filter,
        received_only=received_only,
    )


@router.post("/friends/requests/{request_id}/accept")
async def accept_request(
    request_id: int,
    current_user: dict = Depends(get_current_user),
    friend_repo: FriendRepository = Depends(get_friend_repo),
):
    """接受好友申请"""
    try:
        # 先获取请求详情（用于后续 DM 附言注入）
        f_req = await friend_repo.get_friend_request_by_id(request_id)
        req_message = f_req.message if f_req else None
        req_created_at = f_req.created_at if f_req else None
        req_requester_id = f_req.requester_id if f_req else None
        req_target_type = f_req.target_type if f_req else None
        req_target_id = f_req.target_id if f_req else None

        result = await accept_friend_request(
            friend_repo=friend_repo,
            request_id=request_id,
            user_id=current_user["user_id"],
        )

        # 通知发起者：申请已被接受
        if req_requester_id:
            await _notify_friend_request("request_accepted", {
                "request_id": request_id,
                "accepter_name": current_user.get("username", ""),
            }, req_requester_id)

        # 将附言注入 DM 对话（先发对方附言，再发通过通知）
        if req_message and req_requester_id and req_target_type:
            accepter_uid = current_user["user_id"]
            requester_uid = req_requester_id if req_target_type == "human" else req_requester_id
            await _inject_friend_greeting(
                friend_repo, requester_uid, accepter_uid,
                req_message, created_at=req_created_at,
                prefix="",
            )
            await _inject_friend_greeting(
                friend_repo, accepter_uid, requester_uid,
                "好友申请已通过", created_at=req_created_at,
            )

        return result
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/friends/requests/{request_id}/reject")
async def reject_request(
    request_id: int,
    current_user: dict = Depends(get_current_user),
    friend_repo: FriendRepository = Depends(get_friend_repo),
):
    """拒绝好友申请"""
    try:
        result = await reject_friend_request(
            friend_repo=friend_repo,
            request_id=request_id,
            user_id=current_user["user_id"],
        )
        # 通知发起者：申请已被拒绝
        req = await friend_repo.get_friend_request_by_id(request_id)
        if req:
            await _notify_friend_request("request_rejected", {
                "request_id": request_id,
                "rejecter_name": current_user.get("username", ""),
            }, req.requester_id)
        return result
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.delete("/friends/{friend_type}/{friend_id}")
async def delete_friend(
    friend_type: str,
    friend_id: int,
    current_user: dict = Depends(get_current_user),
    friend_repo: FriendRepository = Depends(get_friend_repo),
):
    """删除好友"""
    if friend_type not in ("human", "ai"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="无效的好友类型")
    try:
        return await remove_friend(
            friend_repo=friend_repo,
            user_id=current_user["user_id"],
            friend_type=friend_type,
            friend_id=friend_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))




async def _inject_friend_greeting(
    friend_repo: FriendRepository,
    from_user_id: int,
    to_user_id: int,
    greeting: str,
    created_at=None,
    prefix: str = "🤝 ",
):
    """好友通过后，将附言注入 DM 对话开头（使用申请时间戳）"""
    from app.chat.dm import get_or_create_dm_session, send_dm_message

    try:
        dm = await get_or_create_dm_session(friend_repo.session, from_user_id, to_user_id)
        await send_dm_message(
            friend_repo.session, dm["session_id"], from_user_id,
            f"{prefix}{greeting}", created_at=created_at,
        )
    except Exception as e:
        logger.warning(f"注入好友附言到 DM 失败: {e}")


# ⚠️ 旧版 POST /dm/{friend_type}/{friend_id} 已移除（v0.1.1 统一 ID 后不再使用）。
# 移除原因：该路由与 dm.py 的 POST /dm/{session_id}/dnd 冲突——
#   FastAPI 将 "dnd" 当作 friend_id 的 int 参数解析，导致 422 错误。
# 私信请使用 POST /dm/{target_user_id}（见 dm.py）。
# 新增 /dm/ 子路由时注意：friends.router 先于 dm.router 注册，任何模糊匹配都会优先命中 friends 的路由。
