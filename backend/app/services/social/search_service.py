"""
搜索服务（v0.1.3: 从 friend_service 中提取，不再依赖好友系统）
"""
import logging
from sqlalchemy import select, func as sqlfunc

from app.repositories.search_repo import SearchRepository

logger = logging.getLogger(__name__)


async def search_entities(
    search_repo: SearchRepository,
    query: str,
    current_user_id: int,
    limit: int = 20,
) -> list[dict]:
    """搜索用户和 AI（无需好友关系即可发起 DM），附带 is_friend 标记"""
    from app.models.user import User
    from app.models.agent import Agent
    from app.models.friendship import Friendship

    results = []
    like_pattern = f"%{query}%"

    # 搜索用户
    user_result = await search_repo.execute(
        select(User).where(
            User.username.ilike(like_pattern),
            User.is_active == True,
            User.type == "human",
        ).limit(limit)
    )
    for user in user_result.scalars().all():
        if user.id == current_user_id:
            continue
        # 检查是否已是好友
        is_friend = False
        friend_check = await search_repo.execute(
            select(Friendship).where(
                Friendship.user_id == current_user_id,
                Friendship.friend_type == "human",
                Friendship.friend_id == user.id,
            )
        )
        is_friend = friend_check.scalar_one_or_none() is not None
        results.append({
            "id": user.id,
            "type": "human",
            "name": user.username,
            "avatar_url": user.avatar_url,
            "owner_name": None,
            "state": None,
            "user_id": user.id,
            "is_friend": is_friend,
        })

    # 搜索 AI（仅返回 discoverable 的 AI）
    agent_result = await search_repo.execute(
        select(Agent).where(
            Agent.name.ilike(like_pattern),
            Agent.discoverable == True,
        ).limit(limit)
    )
    for agent in agent_result.scalars().all():
        from app.models.user import User as UserModel
        owner_result = await search_repo.execute(
            select(UserModel).where(UserModel.id == agent.owner_id)
        )
        owner = owner_result.scalar_one_or_none()
        # 检查是否已是好友（以 AI 的 unified user_id 为 friend_id）
        is_friend = False
        friend_check = await search_repo.execute(
            select(Friendship).where(
                Friendship.user_id == current_user_id,
                Friendship.friend_type == "ai",
                Friendship.friend_id == agent.user_id,
            )
        )
        is_friend = friend_check.scalar_one_or_none() is not None
        results.append({
            "id": agent.user_id,  # 对外统一用 User.id（AI 也是 users 表的一条记录）
            "type": "ai",
            "name": agent.name,
            "avatar_url": agent.avatar_url,
            "owner_name": owner.username if owner else None,
            "state": agent.state,
            "user_id": agent.user_id,
            "is_friend": is_friend,
        })

    return results[:limit]


async def search_groups(
    search_repo: SearchRepository,
    query: str,
    current_user_id: int,
    limit: int = 20,
) -> list[dict]:
    """按群名搜索群聊，附带人数和我是否已在群里。

    只有群主主动开了「可被搜索」的群才会出现——这是暴露入口的开关，
    没开的群连名字都搜不到。
    """
    from app.models.group import Group, GroupMember

    like_pattern = f"%{query}%"
    groups = (await search_repo.execute(
        select(Group).where(
            Group.name.ilike(like_pattern),
            Group.searchable == True,
        ).limit(limit)
    )).scalars().all()
    if not groups:
        return []

    group_ids = [g.id for g in groups]
    member_counts = dict((await search_repo.execute(
        select(GroupMember.group_id, sqlfunc.count()).where(
            GroupMember.group_id.in_(group_ids)
        ).group_by(GroupMember.group_id)
    )).all())
    my_group_ids = set((await search_repo.execute(
        select(GroupMember.group_id).where(
            GroupMember.group_id.in_(group_ids),
            GroupMember.member_type == "human",
            GroupMember.member_id == current_user_id,
        )
    )).scalars().all())

    return [
        {
            "id": group.id,
            "type": "group",
            "name": group.name,
            "avatar_url": group.avatar_url,
            "member_count": member_counts.get(group.id, 0),
            "is_member": group.id in my_group_ids,
            # 前端据此决定按钮是「加入」还是「申请加入」
            "auto_approve_join": bool(group.auto_approve_join),
        }
        for group in groups
    ]
