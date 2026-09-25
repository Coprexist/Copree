"""
群聊相关的 Pydantic 模型
"""
from pydantic import BaseModel, Field


class GroupCreateRequest(BaseModel):
    """创建群聊请求"""
    name: str = Field(..., min_length=1, max_length=100)
    initial_members: list[dict] | None = None  # [{"type": "ai", "id": 1}, ...]


class GroupInviteRequest(BaseModel):
    """邀请成员请求"""
    member_type: str = Field(..., description="human | ai")
    member_id: int
    message: str | None = Field(None, description="邀请附言（仅 human 有效）")


class GroupUpdateRequest(BaseModel):
    """更新群聊设置请求"""
    name: str | None = Field(default=None, min_length=1, max_length=100)
    announcement: str | None = None
    speak_limit_per_minute: int | None = Field(default=None, ge=-1, le=60)
    speak_limit_window_seconds: int | None = Field(default=None, ge=30, le=600)
    is_vector_accelerated: bool | None = None
    avatar_mode: str | None = Field(default=None, pattern=r'^(default|members|custom)$')
    avatar_url: str | None = None
    include_ai_in_avatar: bool | None = None
    # 发现与入群三开关（群主/管理员可改）
    searchable: bool | None = None
    auto_approve_join: bool | None = None
    approve_invites: bool | None = None


class GroupJoinRequestCreate(BaseModel):
    """入群申请请求"""
    message: str | None = Field(default=None, max_length=200, description="申请附言")


class GroupResponse(BaseModel):
    """群聊响应"""
    id: int
    name: str
    owner_type: str
    owner_id: int
    is_vector_accelerated: bool
    is_paused: bool = False
    announcement: str | None = None
    speak_limit_per_minute: int = 0
    speak_limit_window_seconds: int = 120
    my_role: str | None = None
    unread_count: int = 0
    has_mention: bool = False
    last_message_preview: str | None = None
    last_message_at: str | None = None
    dnd_until: str | None = None
    is_pinned: bool = False
    created_at: str | None
    member_count: int = 0
    online_count: int = 0
    member_avatars: list[str] = []
    avatar_mode: str = "default"
    avatar_url: str | None = None
    include_ai_in_avatar: bool = True
    # 发现与入群三开关（默认值＝保持旧行为：搜不到、直接进、邀请免审）
    searchable: bool = False
    auto_approve_join: bool = True
    approve_invites: bool = False


class GroupMemberResponse(BaseModel):
    """群成员响应"""
    group_id: int
    member_type: str
    member_id: int
    role: str
    dnd_until: str | None = None
    joined_at: str | None


class SetDndRequest(BaseModel):
    """设置群聊免打扰请求"""
    group_id: int = Field(..., description="群聊 ID")
    duration_minutes: int | None = Field(default=None, description="免打扰时长（分钟），0 或 null 表示永久")


class AnnouncementRequest(BaseModel):
    """设置群公告请求"""
    content: str = Field(..., min_length=1, max_length=2000)


class RoleChangeRequest(BaseModel):
    """修改成员角色请求"""
    role: str = Field(..., description="admin | member")


class UnreadResponse(BaseModel):
    """单个群聊的未读信息"""
    unread_count: int
    has_mention: bool
    has_announcement: bool
    last_message: dict | None = None  # {content, sender_name, created_at}


class UnreadSummaryItem(BaseModel):
    """单个群聊的未读摘要"""
    group_id: int
    group_name: str
    unread_count: int
    last_message_preview: str
    last_message_at: str | None = None


class UnreadSummaryResponse(BaseModel):
    """未读消息摘要响应"""
    agent_id: int
    groups: list[UnreadSummaryItem]


# ── 联邦共享控制（v0.2.0）──

class FederationShareRequest(BaseModel):
    """群联邦共享/取消共享请求"""
    peer_ids: list[int] = Field(..., min_length=1, description="对等端 ID 列表")


class FederationPeerStatus(BaseModel):
    """单个对等端的联邦共享状态"""
    peer_id: int
    display_name: str
    peer_public_id: str
    is_connected: bool
    is_shared: bool
    federated_id: str = ""


class GroupFederationStatus(BaseModel):
    """群联邦共享状态响应"""
    success: bool = True
    group_id: int
    my_display_name: str = ""
    peers: list[FederationPeerStatus] = []
