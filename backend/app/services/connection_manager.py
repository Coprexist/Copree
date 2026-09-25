"""
WebSocket 连接管理器

职责：管理群聊、私信、用户与常驻通知的 WebSocket 连接池与心跳检测。
这是纯消息通道，不含 AI 决策逻辑。

文档位置：backend/app/services/connection_manager.py
"""

import asyncio
from datetime import datetime, timezone
import logging
import time

from fastapi import WebSocket

from app.utils.error_handler import build_ws_error

logger = logging.getLogger(__name__)

# 会话事件里只有这三类值得弹窗；typing / user_online / state_change / read 都是噪音
NOTIFIABLE_MESSAGE_TYPES = ("message", "ai_response", "announcement")


class ConnectionManager:
    """WebSocket 连接管理器（支持 DND 过滤、错误推送、私信、心跳）

    连接池一律是"一人一集合"：

    - 同一个用户可以同时持有**会话连接**（ChatView 订阅当前群/私信）与**常驻通知连接**
      （站内弹窗，不占订阅位）；
    - 同一个用户开两个标签页、或同时开着群聊与私信，两条连接各自收得到消息，不再互相顶掉。

    常驻通知连接靠 notification_scopes 知道自己要收哪些会话的弹窗——会话消息除了发给
    订阅者，还会按这份范围多推一份 "push" 事件。
    """

    # 心跳参数
    HEARTBEAT_INTERVAL = 30  # ping 发送间隔（秒）
    HEARTBEAT_TIMEOUT = 90   # 无活动超时（秒）

    def __init__(self):
        # 群聊连接：{group_id: {user_id: {websocket, ...}}}
        self.group_connections: dict[int, dict[int, set[WebSocket]]] = {}
        # 私信连接：{session_id: {user_id: {websocket, ...}}}
        self.dm_connections: dict[str, dict[int, set[WebSocket]]] = {}
        # 用户连接：{user_id: {websocket, ...}}
        self.user_connections: dict[int, set[WebSocket]] = {}
        # 常驻通知连接：{user_id: {websocket, ...}}
        self.notification_connections: dict[int, set[WebSocket]] = {}
        # 通知订阅范围：{user_id: {("group", group_id) | ("dm", session_id)}}
        self.notification_scopes: dict[int, set[tuple[str, int | str]]] = {}
        # 心跳活动追踪：{user_id: time.monotonic()}
        self._last_activity: dict[int, float] = {}
        # 第一次ping无回应时记录的时间戳（用于超时断开时写库，避免等满3次才记）
        self._offline_at: dict[int, datetime] = {}

    # ── 连接池小工具：增删都走这里，免得每个方法各写一遍集合清理 ──

    @staticmethod
    def _add_to_room(pool: dict, key, user_id: int, ws: WebSocket) -> None:
        pool.setdefault(key, {}).setdefault(user_id, set()).add(ws)

    @staticmethod
    def _drop_from_room(pool: dict, key, user_id: int, ws: WebSocket) -> None:
        bucket = pool.get(key)
        if bucket is None:
            return
        sockets = bucket.get(user_id)
        if sockets is not None:
            sockets.discard(ws)
            if not sockets:
                bucket.pop(user_id, None)
        if not bucket:
            pool.pop(key, None)

    @staticmethod
    def _drop_from_user_pool(pool: dict, user_id: int, ws: WebSocket) -> None:
        sockets = pool.get(user_id)
        if sockets is None:
            return
        sockets.discard(ws)
        if not sockets:
            pool.pop(user_id, None)

    def _forget_presence_if_offline(self, user_id: int) -> None:
        """该用户一条连接都不剩才清活动记录，否则另一条连接会被心跳误判成离线"""
        if not self.user_connections.get(user_id) and not self.notification_connections.get(user_id):
            self._last_activity.pop(user_id, None)
            self._offline_at.pop(user_id, None)

    def record_activity(self, user_id: int) -> None:
        """记录用户活动时间戳，同时清除之前可能标记的离线时间。
        每次收到 WebSocket 数据（pong 或业务消息）时调用。"""
        self._last_activity[user_id] = time.monotonic()
        self._offline_at.pop(user_id, None)

    def start_heartbeat(self, ws: WebSocket, user_id: int) -> asyncio.Task:
        """
        启动后台心跳检测任务。
        每 HEARTBEAT_INTERVAL 秒发送 ping，
        若连续 HEARTBEAT_TIMEOUT 秒无活动则静默断开连接。
        调用方需在 cleanup 时 cancel 返回的 task。
        """
        self._last_activity[user_id] = time.monotonic()

        async def _beat():
            try:
                while True:
                    await asyncio.sleep(self.HEARTBEAT_INTERVAL)
                    elapsed = time.monotonic() - self._last_activity.get(user_id, 0)
                    # 首次检测到离线（过一个周期没活动）→ 记下此时时间
                    if elapsed >= self.HEARTBEAT_INTERVAL and user_id not in self._offline_at:
                        self._offline_at[user_id] = datetime.now(timezone.utc)
                    # 超时阈值耗尽 → 关连接
                    if elapsed >= self.HEARTBEAT_TIMEOUT:
                        logger.info(f"用户 {user_id} 心跳超时 ({elapsed:.0f}s)，断开连接")
                        await ws.close(code=1000)
                        return
                    await ws.send_json({"type": "ping"})
            except Exception:
                pass  # 连接已断开，静默退出

        return asyncio.create_task(_beat())

    # ── 会话连接（订阅一个群或一个私信） ──

    async def connect(self, ws: WebSocket, group_id: int, user_id: int):
        self._add_to_room(self.group_connections, group_id, user_id, ws)
        self.user_connections.setdefault(user_id, set()).add(ws)
        self.record_activity(user_id)
        logger.info(f"用户 {user_id} 加入群聊 {group_id} 的 WebSocket")

    async def connect_dm(self, ws: WebSocket, session_id: str, user_id: int):
        self._add_to_room(self.dm_connections, session_id, user_id, ws)
        self.user_connections.setdefault(user_id, set()).add(ws)
        self.record_activity(user_id)
        logger.info(f"用户 {user_id} 加入私信 {session_id} 的 WebSocket")

    def disconnect(self, ws: WebSocket, group_id: int, user_id: int):
        self._drop_from_room(self.group_connections, group_id, user_id, ws)
        self._drop_from_user_pool(self.user_connections, user_id, ws)
        self._forget_presence_if_offline(user_id)
        logger.info(f"用户 {user_id} 离开群聊 {group_id} 的 WebSocket")

    def disconnect_dm(self, ws: WebSocket, session_id: str, user_id: int):
        self._drop_from_room(self.dm_connections, session_id, user_id, ws)
        self._drop_from_user_pool(self.user_connections, user_id, ws)
        self._forget_presence_if_offline(user_id)

    def disconnect_all(self, ws: WebSocket, user_id: int) -> None:
        """连接关闭时的兜底清理：这条 socket 可能同时挂在会话池与通知池里"""
        for pool in (self.group_connections, self.dm_connections):
            for key in list(pool):
                self._drop_from_room(pool, key, user_id, ws)
        self._drop_from_user_pool(self.user_connections, user_id, ws)
        self._drop_from_user_pool(self.notification_connections, user_id, ws)
        if not self.notification_connections.get(user_id):
            self.notification_scopes.pop(user_id, None)
        self._forget_presence_if_offline(user_id)

    # ── 常驻通知连接（站内弹窗） ──

    async def subscribe_notifications(
        self,
        ws: WebSocket,
        user_id: int,
        group_ids: list[int],
        session_ids: list[str],
    ) -> None:
        """登记常驻通知连接与订阅范围。成员身份由调用方（ws 层）验过，这里只记范围。"""
        self.notification_connections.setdefault(user_id, set()).add(ws)
        scopes = self.notification_scopes.setdefault(user_id, set())
        scopes.update(("group", gid) for gid in group_ids)
        scopes.update(("dm", sid) for sid in session_ids)
        self.record_activity(user_id)
        logger.info(
            f"用户 {user_id} 常驻通知连接已登记：{len(group_ids)} 个群 / {len(session_ids)} 个私信"
        )

    async def send_notification(self, user_id: int, payload: dict) -> None:
        """只推给常驻通知连接（弹窗事件走这条，不打扰正在会话里的那条连接）"""
        sockets = self.notification_connections.get(user_id)
        if sockets:
            await self._deliver(user_id, sockets, payload)

    # ── 投递 ──

    async def _send(self, ws: WebSocket, payload: dict) -> bool:
        try:
            await ws.send_json(payload)
            return True
        except Exception:
            return False

    async def _deliver(self, user_id: int, sockets: set[WebSocket], payload: dict) -> None:
        for ws in list(sockets):
            if not await self._send(ws, payload):
                logger.warning(f"发送消息给用户 {user_id} 失败，摘掉这条连接")
                self.disconnect_all(ws, user_id)

    async def send_to_user(self, user_id: int, message: dict):
        """向特定用户发送消息（错误通知、好友通知、邀请卡片等）

        会话连接与常驻通知连接都要给：前者做实时更新，后者做站内弹窗。
        """
        for sockets in (self.user_connections.get(user_id), self.notification_connections.get(user_id)):
            if sockets:
                await self._deliver(user_id, sockets, message)

    async def send_error(self, user_id: int, code: str, message: str,
                         tool_call_id: str | None = None):
        """向特定用户发送 WebSocket 错误事件"""
        error_event = build_ws_error(code, message, tool_call_id)
        await self.send_to_user(user_id, error_event)

    async def broadcast_to_group(
        self,
        group_id: int,
        message: dict,
        exclude_user_id: int | None = None,
    ):
        """向群聊广播消息（排除发送者）"""
        for uid, sockets in list(self.group_connections.get(group_id, {}).items()):
            if exclude_user_id is not None and uid == exclude_user_id:
                continue
            await self._deliver(uid, sockets, message)
        await self._push_to_scopes("group", group_id, message, exclude_user_id)

    async def broadcast_to_dm(
        self,
        session_id: str,
        message: dict,
        exclude_user_id: int | None = None,
    ):
        """向私信会话广播消息（通常是推送给对方）"""
        for uid, sockets in list(self.dm_connections.get(session_id, {}).items()):
            if exclude_user_id is not None and uid == exclude_user_id:
                continue
            await self._deliver(uid, sockets, message)
        await self._push_to_scopes("dm", session_id, message, exclude_user_id)

    async def _push_to_scopes(
        self,
        room_kind: str,
        room_id: int | str,
        message: dict,
        exclude_user_id: int | None = None,
    ) -> None:
        """会话消息再推一份给常驻通知连接（他们没订阅这个会话，但要弹窗）。

        只转发真正的消息类事件：typing / user_online / state_change 这些不该弹窗，
        否则一边打字一边弹通知。
        """
        if message.get("type") not in NOTIFIABLE_MESSAGE_TYPES:
            return
        payload = {
            "type": "push",
            "data": {
                "kind": "group_message" if room_kind == "group" else "dm_message",
                "conversation_type": room_kind,
                "conversation_id": room_id,
                "message": message.get("data"),
            },
        }
        for user_id, scopes in list(self.notification_scopes.items()):
            if exclude_user_id is not None and user_id == exclude_user_id:
                continue
            if (room_kind, room_id) in scopes:
                await self.send_notification(user_id, payload)

    def get_offline_timestamp(self, user_id: int) -> datetime | None:
        """获取心跳首次检测到离线的时间戳（仅超时断开时有值，正常断开返回 None）。
        调用后该记录被移除（一次性）。"""
        return self._offline_at.pop(user_id, None)

    def get_online_users(self, group_id: int) -> list[int]:
        return list(self.group_connections.get(group_id, {}).keys())

    def is_user_online(self, user_id: int) -> bool:
        """开着站内通知连接也算在线——以前只有"人正坐在这个会话里"才算"""
        return bool(self.user_connections.get(user_id)) or bool(self.notification_connections.get(user_id))

    def get_online_user_ids(self) -> set[int]:
        return set(self.user_connections.keys()) | set(self.notification_connections.keys())

    async def broadcast_avatar_updated(
        self,
        entity_type: str,
        entity_id: int,
        avatar_url: str,
    ):
        """头像下载完成后通知所有已连接客户端更新消息气泡中的头像 URL"""
        event = {
            "type": "avatar_updated",
            "entity_type": entity_type,
            "entity_id": entity_id,
            "avatar_url": avatar_url,
        }
        seen: set[int] = set()
        for pool in (self.group_connections, self.dm_connections):
            for conns in pool.values():
                for uid, sockets in conns.items():
                    if uid in seen:
                        continue
                    seen.add(uid)
                    await self._deliver(uid, sockets, event)
        for uid in self.get_online_user_ids() - seen:
            await self.send_to_user(uid, event)

    async def broadcast_to_all(self, message: dict):
        """向所有已连接的用户广播消息（用于维护模式等全局通知）"""
        for uid in self.get_online_user_ids():
            await self.send_to_user(uid, message)


connection_manager = ConnectionManager()
