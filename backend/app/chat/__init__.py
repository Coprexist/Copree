"""
聊天核心模块 —— 纯消息管道，不含 AI 决策逻辑

对外接口：ChatApi（AI 模块通过此接口操作聊天世界）
内部模块：
  - gm.py:         群消息创建/投递/广播
  - dm.py:         私信创建/投递
  - connection.py:  WebSocket 连接管理（ConnectionManager）
  - delivery.py:   消息可达性管理（DND/mute/pending）
  - protocol.py:   ChatApi 协议抽象基类
"""

from typing import Any, List, Optional
from datetime import datetime

from app.services.connection_manager import ConnectionManager, connection_manager
from app.chat.protocol import BaseChatApi
from app.chat.gm import (
    create_group,
    get_group,
    list_user_groups,
    add_member,
    get_group_members,
    send_gm_message,
    get_gm_messages,
    gm_message_to_dict,
    gm_message_entry,
    resolve_speaker_names,
    is_member_of_group,
    remove_member,
    leave_group,
    update_last_read,
    update_group_settings,
    change_member_role,
    disband_group,
)
from app.chat.dm import (
    get_or_create_dm_session,
    list_dm_sessions,
    get_dm_session,
    get_dm_messages,
    send_dm_message,
    set_dm_dnd,
    cancel_dm_dnd,
    is_user_in_dm_dnd,
    generate_dm_session_id,
)
from app.chat.delivery import (
    set_group_dnd,
    cancel_group_dnd,
    is_member_in_dnd,
    is_member_muted,
    get_active_silence,
    silence_member,
    consume_silence,
    cancel_member_silence,
    store_pending_message,
    get_pending_messages,
    mark_pending_read,
    check_unread,
)


class ChatApi(BaseChatApi):
    """
    聊天世界的对外接口。

    AI 模块只能通过这个接口操作聊天世界（发消息、查消息、查成员等）。
    这是「Chat 与 AI」之间唯一的契约边界。

    当前实现：in-process 调用 chat/ 子模块。
    未来可替换为 RPC 实现（当 AI 服务独立部署时），调用方代码零改动。
    """

    # ── 群聊 ──

    async def send_gm_message(self, db, group_id, sender_type, sender_id, content,
                              reply_to=None, attachments=None):
        return await send_gm_message(db, group_id, sender_type, sender_id, content,
                                     reply_to=reply_to, attachments=attachments)

    async def get_gm_messages(self, db, group_id, limit=20,
                              before_id=None, after_id=None, after_time=None):
        return await get_gm_messages(db, group_id, limit=limit,
                                     before_id=before_id, after_id=after_id,
                                     after_time=after_time)

    async def gm_message_to_dict(self, message, sender_name=None,
                                 sender_avatar_url=None, sender_state=None):
        return gm_message_to_dict(message, sender_name=sender_name,
                                  sender_avatar_url=sender_avatar_url,
                                  sender_state=sender_state)

    async def resolve_speaker_names(self, db, messages):
        return await resolve_speaker_names(db, messages)

    def gm_message_entry(self, message, *, agent_name, agent_user_id, speaker_name=None, max_len=None):
        return gm_message_entry(message, agent_name=agent_name, agent_user_id=agent_user_id,
                                speaker_name=speaker_name, max_len=max_len)

    async def is_member_of_group(self, db, member_id, member_type, group_id):
        return await is_member_of_group(db, member_id, member_type, group_id)

    async def get_group_members(self, db, group_id):
        return await get_group_members(db, group_id)

    async def get_group(self, db, group_id):
        return await get_group(db, group_id)

    async def list_user_groups(self, db, user_id):
        return await list_user_groups(db, user_id)

    async def add_member(self, db, group_id, member_type, member_id, role="member"):
        return await add_member(db, group_id, member_type, member_id, role=role)

    async def remove_member(self, db, group_id, operator_id, target_type, target_id):
        return await remove_member(db, group_id, operator_id, target_type, target_id)

    async def create_group(self, db, name, owner_type, owner_id, initial_members=None):
        return await create_group(db, name, owner_type, owner_id,
                                   initial_members=initial_members)

    # ── 私信 ──

    async def send_dm_message(self, db, session_id, sender_id, content,
                               reply_to=None, attachments=None,
                               skip_friendship_check=False, message_type="normal"):
        return await send_dm_message(db, session_id, sender_id, content,
                                      reply_to=reply_to, attachments=attachments,
                                      skip_friendship_check=skip_friendship_check,
                                      message_type=message_type)

    async def get_or_create_dm_session(self, db, current_user_id, target_user_id,
                                        skip_friendship_check=False):
        return await get_or_create_dm_session(db, current_user_id, target_user_id,
                                               skip_friendship_check=skip_friendship_check)

    async def get_dm_messages(self, db, session_id, user_id, limit=50,
                               before_id=None, after_id=None):
        return await get_dm_messages(db, session_id, user_id, limit=limit,
                                      before_id=before_id, after_id=after_id)

    async def is_user_in_dm_dnd(self, db, session_id, user_id):
        return await is_user_in_dm_dnd(db, session_id, user_id)

    # ── WebSocket 广播 ──

    def __init__(self):
        self._manager: ConnectionManager | None = None

    def set_manager(self, manager: ConnectionManager):
        self._manager = manager

    async def broadcast_to_group(self, group_id, message, exclude_user_id=None):
        if self._manager:
            await self._manager.broadcast_to_group(group_id, message, exclude_user_id)

    async def broadcast_to_dm(self, session_id, message, exclude_user_id=None):
        if self._manager:
            await self._manager.broadcast_to_dm(session_id, message, exclude_user_id)

    async def send_to_user(self, user_id, message):
        if self._manager:
            await self._manager.send_to_user(user_id, message)

    def is_user_online(self, user_id):
        return self._manager.is_user_online(user_id) if self._manager else False

    def get_online_users(self, group_id):
        return self._manager.get_online_users(group_id) if self._manager else []

    # ── 消息可达性 ──

    async def check_reachability(self, db, agent_id, group_id) -> dict:
        """
        检查 AI 在指定群聊的消息可达性。
        返回: { can_receive, reason, is_dnd, is_muted, is_offline }
        """
        from app.models.agent import Agent as AgentModel
        agent = await db.get(AgentModel, agent_id)
        is_offline = agent is not None and agent.state == "inactive"
        is_dnd = await is_member_in_dnd(db, agent_id, group_id)
        is_muted = await is_member_muted(db, agent_id, group_id)

        can_receive = not is_dnd and not is_muted and not is_offline
        reasons = []
        if is_dnd:
            reasons.append("dnd")
        if is_muted:
            reasons.append("muted")
        if is_offline:
            reasons.append("inactive")

        return {
            "can_receive": can_receive,
            "reason": ",".join(reasons) if reasons else "ok",
            "is_dnd": is_dnd,
            "is_muted": is_muted,
            "is_offline": is_offline,
        }

    # ── 按人静音（只对某个人不响应，连 @ 也不唤醒）──

    async def get_active_silence(self, db, agent_id: int, group_id: int, target_user_id: int):
        return await get_active_silence(db, agent_id, group_id, target_user_id)

    async def consume_silence(self, db, row) -> None:
        return await consume_silence(db, row)

    async def store_pending(self, db, agent_id, group_id, message_id):
        return await store_pending_message(db, agent_id, group_id, message_id)

    async def get_pending(self, db, agent_id, group_id=None, unread_only=True):
        return await get_pending_messages(db, agent_id, group_id=group_id,
                                           unread_only=unread_only)

    async def mark_pending_read(self, db, agent_id, group_id=None):
        return await mark_pending_read(db, agent_id, group_id=group_id)

    async def update_last_read(self, db, group_id, member_type, member_id):
        return await update_last_read(db, group_id, member_type, member_id)

    # ── 缺失接口补充 ──

    async def set_member_dnd(
        self,
        db,
        member_id: int,
        group_id: int,
        until: Optional[datetime] = None,
        member_type: str = "ai",
    ) -> dict:
        if until is None:
            await set_group_dnd(db, member_id, group_id, member_type=member_type)
        else:
            duration = (until - datetime.utcnow()).total_seconds() / 60
            await set_group_dnd(db, member_id, group_id, duration_minutes=int(duration), member_type=member_type)
        return {"success": True}

    async def get_friend_list(self, db, user_id: int) -> List[dict]:
        from app.repositories.friend_repo import SQLAlchemyFriendRepository
        from app.services.social.friend_service import list_friends
        return await list_friends(friend_repo=SQLAlchemyFriendRepository(db), user_id=user_id)

    async def get_user_info(self, db, user_id: int) -> dict:
        from app.models.user import User as UserModel
        user = await db.get(UserModel, user_id)
        if user is None:
            return {}
        return {
            "id": user.id,
            "username": user.username,
            "avatar_url": getattr(user, 'avatar_url', None),
            "language": getattr(user, 'language', 'zh'),
        }

    # ── RPC 适配器预留 ──

    def set_transport(self, transport: Any) -> None:
        """设置传输层适配器（预留接口，支持未来 RPC 替换）"""
        self._transport = transport


# 全局单例 ChatApi 实例
# 聊天服务（ws.py）在初始化时调用 chat_api.set_manager(manager)
# AI 模块通过这个实例操作聊天世界
chat_api = ChatApi()
