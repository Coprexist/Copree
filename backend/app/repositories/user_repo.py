"""
用户仓库接口（Protocol）+ SQLAlchemy 实现。
"""
from typing import Optional, Protocol
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.user import User


class UserRepository(Protocol):
    """用户数据访问接口（结构化类型）。"""

    async def get_by_username(self, username: str) -> Optional[User]: ...
    async def get_by_email(self, email: str) -> Optional[User]: ...
    async def get_by_id(self, user_id: int) -> Optional[User]: ...
    async def count_non_system_users(self) -> int: ...
    def add(self, user: User) -> None: ...
    async def refresh(self, user: User) -> None: ...
    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...
    async def flush(self) -> None: ...


class SQLAlchemyUserRepository:
    """SQLAlchemy 用户仓库实现。"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_username(self, username: str) -> Optional[User]:
        result = await self.session.execute(
            select(User).where(User.username == username)
        )
        return result.scalar_one_or_none()

    async def get_by_email(self, email: str) -> Optional[User]:
        result = await self.session.execute(
            select(User).where(User.email == email)
        )
        return result.scalar_one_or_none()

    async def get_by_id(self, user_id: int) -> Optional[User]:
        return await self.session.get(User, user_id)

    async def count_non_system_users(self) -> int:
        result = await self.session.execute(
            # 系统账号与外部身份都不是"本实例的注册用户"：注册引导（首个用户即管理员）只数真人
            select(func.count(User.id)).where(User.type.notin_(("system", "external")))
        )
        return result.scalar() or 0

    def add(self, user: User) -> None:
        self.session.add(user)

    async def refresh(self, user: User) -> None:
        await self.session.refresh(user)

    async def commit(self) -> None:
        await self.session.commit()

    async def rollback(self) -> None:
        await self.session.rollback()

    async def flush(self) -> None:
        await self.session.flush()
