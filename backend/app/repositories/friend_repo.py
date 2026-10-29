"""
好友仓库接口（Protocol）+ SQLAlchemy 实现。
"""
from datetime import datetime, timezone
from typing import Optional, Protocol
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.friendship import Friendship, FriendshipRequest
from app.models.user import User
from app.models.agent import Agent
from app.models.dm import DMSession


class FriendRepository(Protocol):
    """好友数据访问接口。"""

    async def execute(self, stmt, params=None): ...
    async def get_friendship(self, user_id: int, friend_type: str, friend_id: int) -> Optional[Friendship]: ...
    async def get_friendship_by_id(self, friendship_id: int, user_id: int) -> Optional[Friendship]: ...
    async def get_friend_request_by_id(self, request_id: int) -> Optional[FriendshipRequest]: ...
    async def get_pending_request(self, requester_id: int, target_type: str, target_id: int) -> Optional[FriendshipRequest]: ...
    async def get_reverse_pending_request(self, requester_id: int, target_type: str, target_id: int) -> Optional[FriendshipRequest]: ...
    async def create_friend_request(self, requester_id: int, target_type: str, target_id: int, message: str | None) -> FriendshipRequest: ...
    async def add_friendship(self, user_id: int, friend_type: str, friend_id: int) -> None: ...
    async def delete_friendship(self, friendship: Friendship) -> None: ...
    async def update_request_status(self, request: FriendshipRequest, status: str) -> None: ...
    async def list_friendships(self, user_id: int, limit: int, offset: int) -> list[Friendship]: ...
    async def get_users_by_ids(self, user_ids: list[int]) -> list[User]: ...
    async def get_agents_by_user_ids(self, user_ids: list[int]) -> list[Agent]: ...
    async def get_dm_last_message_at_map(self, session_ids: list[str]) -> dict[str, str]: ...
    async def get_user_by_id(self, user_id: int) -> Optional[User]: ...
    async def get_agent_by_user_id(self, user_id: int) -> Optional[Agent]: ...
    async def get_pending_requests_for_ai(self, agent_user_id: int) -> list[FriendshipRequest]: ...
    async def list_friend_requests(self, user_id: int, status: str, received_only: bool) -> list[FriendshipRequest]: ...
    async def is_friend(self, user_id: int, friend_type: str, friend_id: int) -> bool: ...
    async def flush(self) -> None: ...
    async def refresh(self, obj) -> None: ...
    def add(self, obj) -> None: ...
    async def delete(self, obj) -> None: ...


class SQLAlchemyFriendRepository:
    """SQLAlchemy 好友仓库实现。"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def execute(self, stmt, params=None):
        """共用取数工具（显示名等）只认 execute，仓库要能被直接传进去。"""
        if params is not None:
            return await self.session.execute(stmt, params)
        return await self.session.execute(stmt)

    async def get_friendship(self, user_id: int, friend_type: str, friend_id: int) -> Optional[Friendship]:
        result = await self.session.execute(
            select(Friendship).where(
                Friendship.user_id == user_id,
                Friendship.friend_type == friend_type,
                Friendship.friend_id == friend_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_friendship_by_id(self, friendship_id: int, user_id: int) -> Optional[Friendship]:
        result = await self.session.execute(
            select(Friendship).where(
                Friendship.id == friendship_id,
                Friendship.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_friend_request_by_id(self, request_id: int) -> Optional[FriendshipRequest]:
        result = await self.session.execute(
            select(FriendshipRequest).where(FriendshipRequest.id == request_id)
        )
        return result.scalar_one_or_none()

    async def get_pending_request(self, requester_id: int, target_type: str, target_id: int) -> Optional[FriendshipRequest]:
        result = await self.session.execute(
            select(FriendshipRequest).where(
                FriendshipRequest.requester_id == requester_id,
                FriendshipRequest.target_type == target_type,
                FriendshipRequest.target_id == target_id,
                FriendshipRequest.status == "pending",
            )
        )
        return result.scalar_one_or_none()

    async def get_reverse_pending_request(self, requester_id: int, target_type: str, target_id: int) -> Optional[FriendshipRequest]:
        # 对方 user_id = target_id（target_id 统一为 users.id）
        sent_result = await self.session.execute(
            select(FriendshipRequest).where(
                FriendshipRequest.requester_id == target_id,
                FriendshipRequest.status == "pending",
            )
        )
        for req in sent_result.scalars().all():
            if req.target_id == requester_id:
                return req
        return None

    async def create_friend_request(self, requester_id: int, target_type: str, target_id: int, message: str | None) -> FriendshipRequest:
        req = FriendshipRequest(
            requester_id=requester_id,
            target_type=target_type,
            target_id=target_id,
            message=message,
        )
        self.session.add(req)
        await self.session.flush()
        await self.session.refresh(req)
        return req

    async def add_friendship(self, user_id: int, friend_type: str, friend_id: int) -> None:
        """加好友只加一次。

        关系是"有没有"的事实，不是"加了几次"的流水：已经有这条关系时再插一次会撞
        uq_friendship，而撞在建 flush 上的唯一约束会顺手把整个事务弄死——接受一条早就
        pending 的申请（关系其实几个月前就建好了）正是这样把 AI 的一整轮回复弄没的。

        用 ON CONFLICT DO NOTHING 而不是"先查再插"：唯一约束本来就由数据库守着，
        让插入自己可重复比在应用层补一次 SELECT 更短，也没有查与插之间那个窗口。
        """
        # 冲突目标写成列列表，不写约束名：SQLite 没有具名约束，写成
        # constraint="uq_friendship" 会被渲染成 ON CONFLICT (uq_friendship)，
        # SQLite 把括号里的东西当列名解析，直接报 no such column。列列表两边都认。
        await self.session.execute(
            pg_insert(Friendship)
            .values(user_id=user_id, friend_type=friend_type, friend_id=friend_id)
            .on_conflict_do_nothing(index_elements=["user_id", "friend_type", "friend_id"])
        )

    async def delete_friendship(self, friendship: Friendship) -> None:
        await self.session.delete(friendship)

    async def update_request_status(self, request: FriendshipRequest, status: str) -> None:
        request.status = status
        request.resolved_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await self.session.flush()

    async def list_friendships(self, user_id: int, limit: int, offset: int) -> list[Friendship]:
        result = await self.session.execute(
            select(Friendship)
            .where(Friendship.user_id == user_id)
            .order_by(Friendship.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_users_by_ids(self, user_ids: list[int]) -> list[User]:
        if not user_ids:
            return []
        result = await self.session.execute(select(User).where(User.id.in_(user_ids)))
        return list(result.scalars().all())

    async def get_agents_by_user_ids(self, user_ids: list[int]) -> list[Agent]:
        if not user_ids:
            return []
        result = await self.session.execute(select(Agent).where(Agent.user_id.in_(user_ids)))
        return list(result.scalars().all())

    async def get_dm_last_message_at_map(self, session_ids: list[str]) -> dict[str, str]:
        if not session_ids:
            return {}
        result = await self.session.execute(
            select(DMSession.session_id, DMSession.last_message_at)
            .where(DMSession.session_id.in_(session_ids))
        )
        dm_map = {}
        for row in result.all():
            if row.last_message_at:
                dm_map[row.session_id] = str(row.last_message_at)
        return dm_map

    async def get_user_by_id(self, user_id: int) -> Optional[User]:
        return await self.session.get(User, user_id)

    async def get_agent_by_user_id(self, user_id: int) -> Optional[Agent]:
        result = await self.session.execute(select(Agent).where(Agent.user_id == user_id))
        return result.scalar_one_or_none()

    async def get_pending_requests_for_ai(self, agent_user_id: int) -> list[FriendshipRequest]:
        result = await self.session.execute(
            select(FriendshipRequest).where(
                FriendshipRequest.target_type == "ai",
                FriendshipRequest.target_id == agent_user_id,
                FriendshipRequest.status == "pending",
            ).order_by(FriendshipRequest.created_at.desc()).limit(10)
        )
        return list(result.scalars().all())

    async def list_friend_requests(self, user_id: int, status: str, received_only: bool) -> list[FriendshipRequest]:
        received_result = await self.session.execute(
            select(FriendshipRequest).where(
                FriendshipRequest.target_type == "human",
                FriendshipRequest.target_id == user_id,
                FriendshipRequest.status == status,
            ).order_by(FriendshipRequest.created_at.desc())
        )
        received = list(received_result.scalars().all())
        if received_only:
            return received
        sent_result = await self.session.execute(
            select(FriendshipRequest).where(
                FriendshipRequest.requester_id == user_id,
                FriendshipRequest.status == status,
            ).order_by(FriendshipRequest.created_at.desc())
        )
        sent = list(sent_result.scalars().all())
        return received + sent

    async def is_friend(self, user_id: int, friend_type: str, friend_id: int) -> bool:
        friendship = await self.get_friendship(user_id, friend_type, friend_id)
        return friendship is not None

    async def flush(self) -> None:
        await self.session.flush()

    async def refresh(self, obj) -> None:
        await self.session.refresh(obj)

    def add(self, obj) -> None:
        self.session.add(obj)

    async def delete(self, obj) -> None:
        await self.session.delete(obj)
