"""
好友系统服务
处理好友申请、接受、拒绝、删除、搜索
"""
import logging
from datetime import datetime, timezone

from app.repositories.friend_repo import FriendRepository
from app.models.agent import Agent
from app.models.friendship import Friendship, FriendshipRequest
from app.models.user import User
from app.chat.dm import generate_dm_session_id
from app.utils.display_name import display_name, display_names

logger = logging.getLogger(__name__)


async def send_friend_request(
    *,
    friend_repo: FriendRepository,
    requester_id: int,
    target_type: str,
    target_id: int,
    message: str | None = None,
) -> dict:
    """发送好友申请"""
    # 检查是否已是好友
    if await friend_repo.is_friend(requester_id, target_type, target_id):
        raise ValueError("已经是好友了")

    # 检查是否已有待处理的申请
    existing_req = await friend_repo.get_pending_request(requester_id, target_type, target_id)
    if existing_req:
        raise ValueError("已发送过好友申请，请等待对方处理")

    # 检查对方是否已向自己发送申请（双向申请自动接受）
    reverse = await friend_repo.get_reverse_pending_request(requester_id, target_type, target_id)
    if reverse:
        reverse.status = "accepted"
        reverse.resolved_at = datetime.now(timezone.utc).replace(tzinfo=None)
        r_user_id = reverse.requester_id
        r_type = reverse.target_type
        r_target = reverse.target_id
        await friend_repo.add_friendship(requester_id, target_type, target_id)
        await friend_repo.add_friendship(r_user_id, r_type, r_target)
        await friend_repo.flush()

        # 获取反向申请发起者的名称
        reverse_name = None
        try:
            reverse_name = await display_name(friend_repo, r_user_id)
        except Exception:
            pass

        # 获取目标 AI 的 auto_respond 状态
        auto_respond = None
        if target_type == "ai":
            target_agent = await friend_repo.get_agent_by_user_id(target_id)
            if target_agent:
                auto_respond = target_agent.auto_respond_friend_request
        result = {
            "status": "accepted", "auto": True,
            "message": "对方已向你发送申请，已自动成为好友",
            "reverse_message": reverse.message,
            "reverse_target_name": reverse_name or f"用户{r_user_id}",
        }
        if auto_respond is not None:
            result["auto_respond"] = auto_respond
        return result

    # 如果目标是 AI，检查是否允许接收好友申请
    auto_respond = None
    if target_type == "ai":
        agent_obj = await friend_repo.get_agent_by_user_id(target_id)
        if agent_obj is None:
            raise ValueError("AI 不存在")
        if not agent_obj.allow_friend_requests:
            raise ValueError(f"AI「{agent_obj.name}」已关闭好友申请，无法发送")
        auto_respond = agent_obj.auto_respond_friend_request

    # 创建申请
    req = await friend_repo.create_friend_request(requester_id, target_type, target_id, message)

    logger.info(f"用户 {requester_id} 向 {target_type}:{target_id} 发送好友申请")
    result = {"status": "pending", "request_id": req.id}
    if auto_respond is not None:
        result["auto_respond"] = auto_respond
    return result


async def accept_friend_request(
    *,
    friend_repo: FriendRepository,
    request_id: int,
    user_id: int,
) -> dict:
    """接受好友申请"""
    req = await friend_repo.get_friend_request_by_id(request_id)
    if req is None:
        raise ValueError("申请不存在")

    if req.target_type == "human" and req.target_id != user_id:
        raise ValueError("无权操作此申请")

    if req.status != "pending":
        raise ValueError(f"申请状态为 {req.status}，无法接受")

    req.status = "accepted"
    req.resolved_at = datetime.now(timezone.utc).replace(tzinfo=None)

    await friend_repo.add_friendship(req.requester_id, req.target_type, req.target_id)
    if req.target_type == "human":
        await friend_repo.add_friendship(req.target_id, "human", req.requester_id)
    elif req.target_type == "ai":
        await friend_repo.add_friendship(req.target_id, "human", req.requester_id)

    await friend_repo.flush()
    logger.info(f"好友申请 {request_id} 已接受")
    return {"status": "accepted"}


async def reject_friend_request(
    *,
    friend_repo: FriendRepository,
    request_id: int,
    user_id: int,
) -> dict:
    """拒绝好友申请"""
    req = await friend_repo.get_friend_request_by_id(request_id)
    if req is None:
        raise ValueError("申请不存在")

    is_target = (req.target_type == "human" and req.target_id == user_id)
    is_requester = (req.requester_id == user_id)
    if not is_target and not is_requester:
        raise ValueError("无权操作此申请")

    if req.status != "pending":
        raise ValueError(f"申请状态为 {req.status}，无法拒绝")

    req.status = "rejected"
    req.resolved_at = datetime.now(timezone.utc).replace(tzinfo=None)

    await friend_repo.flush()
    logger.info(f"好友申请 {request_id} 已拒绝")
    return {"status": "rejected"}


async def remove_friend(
    *,
    friend_repo: FriendRepository,
    user_id: int,
    friend_type: str,
    friend_id: int,
) -> dict:
    """删除好友"""
    friendship = await friend_repo.get_friendship(user_id, friend_type, friend_id)
    if friendship is None:
        raise ValueError("好友关系不存在")

    await friend_repo.delete_friendship(friendship)

    if friend_type == "human":
        reverse = await friend_repo.get_friendship(friend_id, "human", user_id)
        if reverse:
            await friend_repo.delete_friendship(reverse)

    await friend_repo.flush()
    logger.info(f"用户 {user_id} 删除了好友 {friend_type}:{friend_id}")
    return {"status": "removed"}


async def list_friends(
    *,
    friend_repo: FriendRepository,
    user_id: int,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """获取好友列表（批量查询优化，避免 N+1）"""
    friendships = await friend_repo.list_friendships(user_id, limit, offset)
    if not friendships:
        return []

    all_friend_ids = [f.friend_id for f in friendships]
    users = await friend_repo.get_users_by_ids(all_friend_ids)
    users_map = {u.id: u for u in users}

    ai_friend_ids = [f.friend_id for f in friendships if f.friend_type == "ai"]
    agents = await friend_repo.get_agents_by_user_ids(ai_friend_ids)
    agents_by_user_id = {a.user_id: a for a in agents}

    session_ids = []
    for f in friendships:
        friend_user_id = f.friend_id
        if friend_user_id:
            session_ids.append(generate_dm_session_id(user_id, friend_user_id))
    dm_map = await friend_repo.get_dm_last_message_at_map(session_ids)

    friends = []
    for f in friendships:
        name = f"未知:{f.friend_id}"
        state = None
        friend_user_id = f.friend_id
        avatar_url = None
        status_text = None
        status_color = None

        u = users_map.get(f.friend_id)
        if u:
            name = u.username
            avatar_url = u.avatar_url
            status_text = getattr(u, 'status_text', None)
            status_color = getattr(u, 'status_color', None)

        if f.friend_type == "ai":
            a = agents_by_user_id.get(f.friend_id)
            if a:
                name = a.name
                state = a.state
                avatar_url = a.avatar_url or avatar_url
                status_text = getattr(a, 'status_text', None) or status_text
                status_color = getattr(a, 'status_color', None) or status_color

        last_dm_at = None
        if friend_user_id:
            sid = generate_dm_session_id(user_id, friend_user_id)
            last_dm_at = dm_map.get(sid)

        friends.append({
            "id": f.id,
            "friend_type": f.friend_type,
            "friend_id": f.friend_id,
            "friend_user_id": friend_user_id,
            "friend_name": name,
            "state": state,
            "avatar_url": avatar_url,
            "status_text": status_text,
            "status_color": status_color,
            "is_priority": bool(f.is_priority),
            "created_at": str(f.created_at) if f.created_at else None,
            "last_dm_at": last_dm_at,
        })

    return friends


async def get_pending_friend_requests_for_ai(
    *,
    friend_repo: FriendRepository,
    agent_user_id: int,
) -> list[dict]:
    """AI 视角：待处理的好友申请（target = 该 AI 的 user_id，status=pending）"""
    rows = await friend_repo.get_pending_requests_for_ai(agent_user_id)
    result = []
    for r in rows:
        name = await display_name(friend_repo, r.requester_id)
        result.append({
            "id": r.id,
            "requester_id": r.requester_id,
            "name": name or f"用户{r.requester_id}",
            "message": r.message or "",
        })
    return result


async def list_friend_requests(
    *,
    friend_repo: FriendRepository,
    user_id: int,
    status: str = "pending",
    received_only: bool = False,
) -> list[dict]:
    """获取好友申请列表（收到 + 发出的），批量查询避免 N+1"""
    all_requests = await friend_repo.list_friend_requests(user_id, status, received_only)
    if not all_requests:
        return []

    requester_ids = list({r.requester_id for r in all_requests})
    users = await friend_repo.get_users_by_ids(requester_ids)
    requester_names = await display_names(friend_repo, requester_ids)
    users_map = {u.id: (requester_names[u.id], u.avatar_url) for u in users}

    sent_reqs = [r for r in all_requests if r.requester_id == user_id]
    human_target_ids = [r.target_id for r in sent_reqs if r.target_type == "human"]
    ai_target_ids = [r.target_id for r in sent_reqs if r.target_type == "ai"]

    target_human_map = {}
    if human_target_ids:
        human_targets = await friend_repo.get_users_by_ids(human_target_ids)
        target_human_map = {u.id: (u.username, u.avatar_url) for u in human_targets}

    target_ai_map = {}
    if ai_target_ids:
        ai_targets = await friend_repo.get_agents_by_user_ids(ai_target_ids)
        target_ai_map = {a.user_id: (a.name, a.avatar_url, a.auto_respond_friend_request) for a in ai_targets}

    results = []
    for req in all_requests:
        ru = users_map.get(req.requester_id)
        requester_name = ru[0] if ru else f"用户:{req.requester_id}"
        requester_avatar = ru[1] if ru else None

        target_name = None
        target_avatar = None
        target_auto_respond = None
        if req.requester_id == user_id:
            if req.target_type == "human":
                tu = target_human_map.get(req.target_id)
                if tu:
                    target_name, target_avatar = tu
            elif req.target_type == "ai":
                ta = target_ai_map.get(req.target_id)
                if ta:
                    target_name, target_avatar, target_auto_respond = ta

        result_item = {
            "id": req.id,
            "requester_id": req.requester_id,
            "requester_name": requester_name,
            "requester_avatar_url": requester_avatar,
            "target_type": req.target_type,
            "target_id": req.target_id,
            "target_name": target_name,
            "target_avatar_url": target_avatar,
            "is_priority": bool(req.is_priority),
            "status": req.status,
            "message": req.message,
            "direction": "received" if req.target_id == user_id else "sent",
            "created_at": str(req.created_at) if req.created_at else None,
            "resolved_at": str(req.resolved_at) if req.resolved_at else None,
        }
        if target_auto_respond is not None:
            result_item["auto_respond_friend_request"] = target_auto_respond
        results.append(result_item)

    return results


async def search_entities(
    *,
    friend_repo: FriendRepository,
    query: str,
    current_user_id: int,
    limit: int = 20,
) -> list[dict]:
    """搜索用户和 AI"""
    users, agents = await friend_repo.search_users_and_agents(query, current_user_id, limit)
    results = []

    for user in users:
        if user.id == current_user_id:
            continue
        is_friend = await friend_repo.is_friend(current_user_id, "human", user.id)
        results.append({
            "id": user.id,
            "type": "human",
            "name": user.username,
            "avatar_url": user.avatar_url,
            "owner_name": None,
            "is_friend": is_friend,
            "state": None,
        })

    for agent in agents:
        owner = await friend_repo.get_user_by_id(agent.owner_id)
        is_friend = await friend_repo.is_friend(current_user_id, "ai", agent.user_id)
        results.append({
            "id": agent.user_id,
            "type": "ai",
            "name": agent.name,
            "avatar_url": agent.avatar_url,
            "owner_name": owner.username if owner else None,
            "is_friend": is_friend,
            "state": agent.state,
            "auto_respond_friend_request": agent.auto_respond_friend_request,
            "user_id": agent.user_id,
        })

    return results[:limit]


async def trigger_ai_auto_respond(
    *,
    friend_repo: FriendRepository,
    agent: Agent,
    requester_id: int,
    requester_name: str,
    message: str,
) -> None:
    """触发 AI 自动响应好友申请（由路由调用，封装 alarm 层的 db 访问）"""
    from app.ai.alarm import _process_friend_request_event
    await _process_friend_request_event(friend_repo.session, {
        "agent_id": agent.id,
        "requester_id": requester_id,
        "requester_name": requester_name,
        "message": message,
    })
