"""
群消息投递 —— 消息落库后「谁来收、怎么收」的唯一定义

为什么单独一层：这套逻辑原本长在 WebSocket 路由里，而它跟"WebSocket"其实没关系——
消息从哪进来（网页端 / QQ 通道 / 将来的微信通道）不该改变投递规则与唤醒规则。
外部通道一旦自己拼一套，就会出现"网页上 AI 收得到、从 QQ 来的收不到"这种差异，
并且会一直长出来。所以：**所有入站通道共用这一条路径**。

顺序（与原来完全一致，改动只是搬了家）：
    message_view        → 消息视图（发送者名字/头像/状态以库为准）
    fanout_group_message→ 在线 AI 直推 / 不在线或 DND 暂存（判定收在 delivery_decision）
    （调用方 commit）
    forward_group_message_federated → 联邦群异步转发（fire-and-forget）
    wake_group_ai       → 推给 AI 回复 worker（仅人类消息）
    maybe_vectorize_group_message   → 向量加速群才入队
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.delivery import delivery_decision, store_pending_message
from app.chat.gm import gm_message_to_dict
from app.models.agent import Agent as AgentModel
from app.models.group import GroupMember as GroupMemberModel
from app.utils.pure.timeutil import utc_now

logger = logging.getLogger(__name__)


async def message_view(db: AsyncSession, message, sender_name: str | None = None) -> dict:
    """群消息 → 通道统一视图（发送者名字与头像以库为准，WS 上报值可能过期）"""
    from app.models.user import User as UserModel

    name = sender_name
    avatar = None
    state = None
    try:
        row = (await db.execute(
            select(UserModel).where(UserModel.id == message.sender_id)
        )).scalar_one_or_none()
        if row:
            name = row.username
            avatar = row.avatar_url
            if row.type == "ai":
                agent = (await db.execute(
                    select(AgentModel).where(AgentModel.user_id == message.sender_id)
                )).scalar_one_or_none()
                if agent:
                    avatar = agent.avatar_url or avatar
                    state = agent.state
    except Exception as e:
        logger.error(f"获取发送者信息失败: {e}", exc_info=True)

    return gm_message_to_dict(message, sender_name=name, sender_avatar_url=avatar, sender_state=state)


async def broadcast_group_message(group_id: int, msg_data: dict) -> None:
    """把落库后的群消息推给这个群的所有在线连接（人和 AI 都在同一张连接表里）。

    网页端发消息走这里，QQ 这类外部通道也必须走这里 —— 少了这一步，群里的人要刷新
    才能看到外部来的消息（用户 2026-09-25 实测：QQ 群里说了话，Copree 侧界面上不出现，
    刷新之后才出现）。fanout_group_message 管的是 AI 成员与离线暂存，不是同一件事。
    """
    from app.routers.ws import manager  # 懒导入：ws 路由反向用本模块，顶层导入会成环

    try:
        await manager.broadcast_to_group(group_id, {"type": "message", "data": msg_data})
    except Exception as e:
        logger.warning(f"群 {group_id} 消息广播失败: {e}")


async def fanout_group_message(
    db: AsyncSession, group_id: int, message, content: str, msg_data: dict | None = None
) -> None:
    """把落库后的群消息投给群里的 AI 成员：在线且不需暂存就直推，否则暂存等它自己拉。

    依据文档的可达性矩阵（docs/chat_service/design/chat_service_design.md §4.2）与 cpec.md：
    在线且不 DND → 直推；@提及 连 DND 也穿透；DND/暂停 → 暂存；**不在线 → 一律暂存**。
    判定收在 delivery_decision 一处。
    """
    from app.routers.ws import manager  # 懒导入：ws 路由反向用本模块，顶层导入会成环

    if msg_data is None:
        msg_data = await message_view(db, message)

    try:
        member_rows = (await db.execute(
            select(GroupMemberModel.member_id, GroupMemberModel.dnd_until).where(
                GroupMemberModel.group_id == group_id,
                GroupMemberModel.member_type == "ai",
            )
        )).all()
        member_ids = [r[0] for r in member_rows if r[0] != message.sender_id]

        if member_ids:
            # member_id 就是 user_id（v2.0.0 统一口径）；而暂存表的 agent_id 是
            # agents.id，所以按 user_id 查回 agent —— 拿 user_id 当 agent.id 用会错配
            agent_rows = (await db.execute(
                select(AgentModel.id, AgentModel.user_id, AgentModel.name,
                       AgentModel.is_paused).where(
                    AgentModel.user_id.in_(member_ids),
                )
            )).all()
            agent_by_user = {r[1]: r for r in agent_rows}
            dnd_by_user = {r[0]: r[1] for r in member_rows}

            from app.utils.text import extract_mentions, mentions_user
            mentioned_names = extract_mentions(content)
            is_all_call = "@all" in content.lower() or "@ai" in content.lower()
            now = utc_now()

            for uid in member_ids:
                agent_row = agent_by_user.get(uid)
                sockets = list(manager.group_connections.get(group_id, {}).get(uid) or ())
                dnd_until = dnd_by_user.get(uid)
                # dnd_until 为空 = 没设免打扰（旧代码把「空」当成「永远免打扰」，反了）
                in_dnd = bool(agent_row and agent_row[3]) or (
                    dnd_until is not None and dnd_until > now
                )
                is_mentioned = bool(
                    agent_row and (
                        agent_row[2] in mentioned_names          # 旧写法：@名字
                        or mentions_user(content, agent_row[1])  # 新写法：<@!id>
                        or is_all_call
                    )
                )

                if delivery_decision(
                    online=bool(sockets), in_dnd=in_dnd, mentioned=is_mentioned,
                ) == "push":
                    for user_ws in sockets:
                        try:
                            await user_ws.send_json({"type": "message", "conversation_type": "group", "data": msg_data})
                        except Exception as e:
                            logger.warning(f"发送消息给用户 {uid} 失败: {e}")
                    continue

                # 不在线 / DND / 暂停 → 暂存，等它回来 view_unread 自己拉
                if agent_row is None:
                    continue          # 群成员没有对应 agent（脏数据）：无处可存
                try:
                    await store_pending_message(
                        db, agent_id=agent_row[0], group_id=group_id,
                        message_id=message.id,
                    )
                except Exception as e:
                    logger.warning(f"暂存消息给 AI {uid} 失败: {e}")
    except Exception as e:
        logger.error(f"广播消息给群成员失败: {e}", exc_info=True)
        # 广播失败不阻断消息已创建的事实


def wake_group_ai(group_id: int, message, content: str) -> bool:
    """推给 AI 回复 worker（仅人类消息；AI 自己发的消息不再触发，避免自问自答）"""
    if getattr(message, "sender_type", None) != "human":
        return False
    from app.ai.response_worker import message_queue

    try:
        message_queue.put_nowait({
            "conversation_type": "group",
            "group_id": group_id,
            "message_id": message.id,
            "content": content,
            "sender_type": message.sender_type,
            "sender_id": message.sender_id,
            "chain_depth": 0,
        })
        logger.info(f"📨 消息已推入 AI 队列: group={group_id}, msg={message.id}, queue_size={message_queue.qsize()}")
        return True
    except asyncio.QueueFull:
        logger.warning("AI 回复队列已满，丢弃事件")
        return False


async def forward_group_message_federated(
    group_id: int, msg_data: dict, db: AsyncSession | None = None
) -> None:
    """联邦群：异步转发给共享此群的对等端（失败不影响本地消息）

    优先复用调用方的 session（发消息的路上不再多开一条连接）。
    """
    try:
        from app.services.federation.federation_service import is_group_federated as check_grp_fed

        if db is not None:
            if not await check_grp_fed(db, group_id):
                return
        else:
            from app.database import async_session

            async with async_session() as session:
                if not await check_grp_fed(session, group_id):
                    return
        from app.services.federation.federation_manager import federation_manager as fed_mgr

        asyncio.create_task(fed_mgr.forward_message(group_id, msg_data))
    except Exception:
        pass


async def maybe_vectorize_group_message(db: AsyncSession, group_id: int, message) -> None:
    """向量加速群：把新消息送进 embedding 队列（队列满则丢，不影响主流程）"""
    try:
        from app.models.group import Group as GroupModel

        is_accelerated = (await db.execute(
            select(GroupModel.is_vector_accelerated).where(GroupModel.id == group_id)
        )).scalar_one_or_none()
        if not is_accelerated:
            return
        from app.services.memory.vector_pipeline import embedding_queue

        try:
            embedding_queue.put_nowait({"group_id": group_id, "message_id": message.id})
        except asyncio.QueueFull:
            pass
    except Exception as e:
        logger.warning(f"向量化 pipeline 触发失败: {e}")
