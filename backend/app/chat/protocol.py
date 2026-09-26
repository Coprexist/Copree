from abc import ABC, abstractmethod
from typing import Any, List, Optional
from datetime import datetime


class BaseChatApi(ABC):
    """ChatApi 协议抽象基类 — 聊天世界的统一接口契约"""

    @abstractmethod
    async def send_gm_message(
        self,
        db,
        group_id: int,
        sender_type: str,
        sender_id: int,
        content: str,
        reply_to: Optional[int] = None,
        attachments: Optional[List[str]] = None,
    ) -> Any: ...

    @abstractmethod
    async def set_member_dnd(
        self,
        db,
        member_id: int,
        group_id: int,
        until: Optional[datetime] = None,
        member_type: str = "ai",
    ) -> dict: ...

    @abstractmethod
    async def get_friend_list(self, db, user_id: int) -> List[dict]: ...

    @abstractmethod
    async def get_user_info(self, db, user_id: int) -> dict: ...

    @abstractmethod
    async def is_member_of_group(self, db, member_id: int, member_type: str, group_id: int) -> bool: ...

    @abstractmethod
    async def get_group_members(self, db, group_id: int) -> List[dict]: ...

    @abstractmethod
    async def get_group(self, db, group_id: int) -> dict | None: ...

    @abstractmethod
    async def list_user_groups(self, db, user_id: int) -> List[dict]: ...

    @abstractmethod
    async def add_member(self, db, group_id: int, member_type: str, member_id: int, role: str = "member") -> dict: ...

    @abstractmethod
    async def remove_member(self, db, group_id: int, operator_id: int, target_type: str, target_id: int) -> None: ...

    @abstractmethod
    async def create_group(self, db, name: str, owner_type: str, owner_id: int, initial_members: Optional[List[dict]] = None) -> dict: ...

    @abstractmethod
    async def send_dm_message(self, db, session_id: str, sender_id: int, content: str, reply_to: Optional[int] = None, attachments: Optional[List[str]] = None) -> dict: ...

    @abstractmethod
    async def get_or_create_dm_session(self, db, current_user_id: int, target_user_id: int) -> dict: ...

    @abstractmethod
    async def get_dm_messages(self, db, session_id: str, user_id: int, limit: int = 50, before_id: Optional[int] = None, after_id: Optional[int] = None) -> List[dict]: ...

    @abstractmethod
    async def is_user_in_dm_dnd(self, db, session_id: str, user_id: int) -> bool: ...

    @abstractmethod
    async def check_reachability(self, db, agent_id: int, group_id: int) -> dict: ...

    @abstractmethod
    async def get_active_silence(self, db, agent_id: int, group_id: int, target_user_id: int): ...

    @abstractmethod
    async def consume_silence(self, db, row) -> None: ...

    @abstractmethod
    async def store_pending(self, db, agent_id: int, group_id: int, message_id: int) -> dict: ...

    @abstractmethod
    async def get_pending(self, db, agent_id: int, group_id: Optional[int] = None, unread_only: bool = True) -> List[dict]: ...

    @abstractmethod
    async def mark_pending_read(self, db, agent_id: int, group_id: Optional[int] = None) -> None: ...

    @abstractmethod
    async def update_last_read(self, db, group_id: int, member_type: str, member_id: int) -> bool: ...

    def set_transport(self, transport: Any) -> None:
        """设置传输层适配器（预留接口，支持未来 RPC 替换）"""
        pass