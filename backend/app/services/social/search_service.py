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
    """搜索用户和 AI —— 唯一入口：站内搜索接口与 AI 的 search_users 工具都走这里。

    - 人只出 is_active 的人类账号；AI 只出开了「可被发现」且有统一 ID 的
      （没统一 ID 的加不了好友、发不了私信，进结果只会给前端一个点不动的条目）
    - 自己不进结果：人按 user_id 排掉，调用的 AI 也按自己的 user_id 排掉
    - 好友关系与制作者名字各一次批查询——原先按人头各查一次，20 条结果要发 40 条 SQL
    """
    from app.models.user import User
    from app.models.agent import Agent
    from app.models.friendship import Friendship

    like_pattern = f"%{query}%"

    users = list((await search_repo.execute(
        select(User).where(
            User.username.ilike(like_pattern),
            User.is_active == True,
            User.type == "human",
        ).limit(limit)
    )).scalars().all())

    agents = list((await search_repo.execute(
        select(Agent).where(
            Agent.name.ilike(like_pattern),
            Agent.discoverable == True,
            Agent.user_id.isnot(None),
            Agent.user_id != current_user_id,
        ).limit(limit)
    )).scalars().all())

    friend_pairs: set[tuple[str, int]] = set()
    ids = [u.id for u in users] + [a.user_id for a in agents]
    if ids and current_user_id:
        rows = await search_repo.execute(
            select(Friendship.friend_type, Friendship.friend_id).where(
                Friendship.user_id == current_user_id,
                Friendship.friend_id.in_(ids),
            )
        )
        friend_pairs = {(friend_type, friend_id) for friend_type, friend_id in rows.all()}

    owner_ids = {a.owner_id for a in agents if a.owner_id}
    owners: dict[int, str] = {}
    if owner_ids:
        rows = await search_repo.execute(
            select(User.id, User.username).where(User.id.in_(owner_ids))
        )
        owners = {uid: username for uid, username in rows.all()}

    results = []
    for user in users:
        if user.id == current_user_id:
            continue
        results.append({
            "id": user.id,
            "type": "human",
            "name": user.username,
            "avatar_url": user.avatar_url,
            "owner_name": None,
            "state": None,
            "user_id": user.id,
            "is_friend": ("human", user.id) in friend_pairs,
        })

    for agent in agents:
        results.append({
            "id": agent.user_id,  # 对外统一用 User.id（AI 也是 users 表的一条记录）
            "type": "ai",
            "name": agent.name,
            "avatar_url": agent.avatar_url,
            "owner_name": owners.get(agent.owner_id),
            "state": agent.state,
            "user_id": agent.user_id,
            "is_friend": ("ai", agent.user_id) in friend_pairs,
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
