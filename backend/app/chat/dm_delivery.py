"""
私信投递 —— 私信落库后「谁来收、怎么收」的唯一定义（与 group_delivery 对称）

为什么单独一层：同群消息——这套逻辑原本长在 WebSocket 路由里，但它跟 WebSocket 没关系。
网页端与外部通道（QQ 通道）必须共用：否则"网页上 AI 收得到、从 QQ 来的收不到"会一直长出来。

顺序：
    fanout_dm_message  → 推给会话另一端（调用方 commit 前）
    wake_dm_ai         → 唤醒 AI 回复 worker（调用方 commit 后）
    forward_dm_federated → 联邦转发（fire-and-forget）
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


async def fanout_dm_message(session_id: str, msg: dict, sender_id: int) -> None:
    """把私信推给会话另一端（排除发送者自己）"""
    from app.routers.ws import manager  # 懒导入：ws 路由反向用本模块，顶层导入会成环

    await manager.broadcast_to_dm(
        session_id,
        {"type": "message", "conversation_type": "dm", "data": msg},
        exclude_user_id=sender_id,
    )


def wake_dm_ai(
    session_id: str, msg: dict, sender_id: int, sender_type: str = "human"
) -> bool:
    """推给 AI 回复 worker（仅人类发的私信；AI 自己发的不再触发，避免自问自答）"""
    if sender_type != "human":
        return False
    from app.ai.response_worker import message_queue

    try:
        message_queue.put_nowait({
            "conversation_type": "dm",
            "session_id": session_id,
            "message_id": msg.get("id"),
            "content": msg.get("content") or "",
            "sender_type": sender_type,
            "sender_id": sender_id,
            "chain_depth": 0,
        })
        return True
    except asyncio.QueueFull:
        logger.warning("AI 回复队列已满，丢弃 DM 事件")
        return False


async def forward_dm_federated(session_id: str, msg: dict) -> None:
    """联邦：把私信转发给共享此会话的对等端（失败不影响本地消息）"""
    try:
        from app.services.federation.federation_manager import federation_manager as fed_mgr

        asyncio.create_task(fed_mgr.forward_dm_message(session_id, msg))
    except Exception:
        pass
