"""
好友仓库接口（Protocol）+ SQLAlchemy 实现。
"""
from datetime import datetime
from typing import Optional, Protocol
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.friendship import Friendship, FriendshipRequest
from app.models.user import User
from app.models.agent import Agent
from app.models.dm import DMSession, DMMessage


class FriendRepository(Protocol):
    """好友数据访问接口。"""

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
    async def search_users_and_agents(self, query: str, current_user_id: int, limit: int) -> tuple[list[User], list[Agent]]: ...
    async def is_friend(self, user_id: int, friend_type: str, friend_id: int) -> bool: ...
    async def get_or_create_dm_session(self, user_id_a: int, user_id_b: int, skip_friendship_check: bool = False) -> str: ...
    async def send_dm_message(self, session_id: str, sender_id: int, content: str, created_at: datetime | None = None) -> None: ...
    async def flush(self) -> None: ...
    async def refresh(self, obj) -> None: ...
    def add(self, obj) -> None: ...
    async def delete(self, obj) -> None: ...


class SQLAlchemyFriendRepository:
    """SQLAlchemy 好友仓库实现。"""

    def __init__(self, session: AsyncSession):
        self.session = session

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
        self.session.add(Friendship(user_id=user_id, friend_type=friend_type, friend_id=friend_id))

    async def delete_friendship(self, friendship: Friendship) -> None:
        await self.session.delete(friendship)

    async def update_request_status(self, request: FriendshipRequest, status: str) -> None:
        request.status = status
        request.resolved_at = __import__('datetime').datetime.now(__import__('datetime').timezone.utc).replace(tzinfo=None)
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

    async def search_users_and_agents(self, query: str, current_user_id: int, limit: int) -> tuple[list[User], list[Agent]]:
        like_pattern = f"%{query}%"
        user_result = await self.session.execute(
            select(User).where(
                User.username.ilike(like_pattern),
                User.is_active == True,
                User.type == "human",
            ).limit(limit)
        )
        users = list(user_result.scalars().all())
        agent_result = await self.session.execute(
            select(Agent).where(Agent.name.ilike(like_pattern)).limit(limit)
        )
        agents = list(agent_result.scalars().all())
        return users, agents

    async def is_friend(self, user_id: int, friend_type: str, friend_id: int) -> bool:
        friendship = await self.get_friendship(user_id, friend_type, friend_id)
        return friendship is not None

    async def get_or_create_dm_session(self, user_id_a: int, user_id_b: int, skip_friendship_check: bool = False) -> str:
        """获取或创建 DM 会话，返回 session_id"""
        from app.chat.dm import generate_dm_session_id, _require_friendship
        if user_id_a == user_id_b:
            raise ValueError("不能和自己私信")

        target = await self.session.execute(select(User).where(User.id == user_id_b))
        if target.scalar_one_or_none() is None:
            raise ValueError("目标用户不存在")

        session_id = generate_dm_session_id(user_id_a, user_id_b)
        result = await self.session.execute(
            select(DMSession).where(DMSession.session_id == session_id)
        )
        session = result.scalar_one_or_none()
        if session is None:
            if not skip_friendship_check:
                await _require_friendship(self.session, user_id_a, user_id_b, initiator_id=user_id_a)
            user_ids = sorted([user_id_a, user_id_b])
            session = DMSession(
                session_id=session_id,
                user1_id=user_ids[0],
                user2_id=user_ids[1],
            )
            self.session.add(session)
            await self.session.flush()
        return session_id

    async def send_dm_message(
        self, session_id: str, sender_id: int, content: str,
        created_at: datetime | None = None,
    ) -> None:
        """发送 DM 消息（好友附言注入用，跳过好友校验）"""
        from datetime import datetime as _dt, timezone
        result = await self.session.execute(
            select(DMSession).where(DMSession.session_id == session_id)
        )
        session = result.scalar_one_or_none()
        if session is None:
            raise ValueError("会话不存在")

        now = _dt.now(timezone.utc).replace(tzinfo=None)
        msg = DMMessage(
            session_id=session_id,
            sender_id=sender_id,
            content=content.strip(),
            message_type="normal",
            created_at=created_at or now,
        )
        self.session.add(msg)
        await self.session.flush()

        session.last_message_id = msg.id
        session.last_message_at = created_at or now
        await self.session.flush()

    async def flush(self) -> None:
        await self.session.flush()

    async def refresh(self, obj) -> None:
        await self.session.refresh(obj)

    def add(self, obj) -> None:
        self.session.add(obj)

    async def delete(self, obj) -> None:
        await self.session.delete(obj)
