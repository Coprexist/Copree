"""
好友系统 Pydantic Schema
"""
from pydantic import BaseModel, Field


class FriendRequestCreate(BaseModel):
    """发送好友申请"""
    target_type: str = Field(..., description="目标类型: human 或 ai")
    target_id: int = Field(..., gt=0, description="目标 ID")
    message: str | None = Field(None, max_length=200, description="申请附言")


class FriendRequestResponse(BaseModel):
    """好友申请响应"""
    id: int
    requester_id: int
    requester_name: str | None = None
    requester_avatar_url: str | None = None
    target_type: str
    target_id: int
    target_name: str | None = None  # 目标名称（发出的申请用）
    target_avatar_url: str | None = None
    auto_respond_friend_request: bool | None = None
    is_priority: bool = False  # 是否特别关心
    status: str
    message: str | None = None
    direction: str | None = None  # received 或 sent
    created_at: str | None = None
    resolved_at: str | None = None


class FriendResponse(BaseModel):
    """好友列表项"""
    id: int
    friend_type: str
    friend_id: int
    friend_user_id: int | None = None  # 好友在 users 表中的 id（用于私信）
    friend_name: str
    avatar_url: str | None = None
    state: str | None = None  # AI 在线状态
    is_priority: bool = False  # 是否特别关心
    created_at: str | None = None
    last_dm_at: str | None = None  # 最近一次私信时间

