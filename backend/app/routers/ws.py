"""
WebSocket 实时通信处理器
支持 DND 过滤、消息暂存、错误推送
"""
import asyncio
import json
import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from sqlalchemy import select, func, update as sa_update

# 常驻通知最多挂多少个私信会话（按最近消息排序取前 N；几百个考古会话没必要挂）
NOTIFICATION_SESSION_LIMIT = 200
from app.database import async_session
from app.models.user import User as UserModel
from app.models.group import GroupMember as GroupMemberModel
from app.utils.auth import decode_access_token
from app.utils.error_handler import build_ws_error, log_error
from app.chat.connection import ConnectionManager
from app.chat import chat_api
from app.chat.gm import send_gm_message, is_group_member
from app.chat.dm_delivery import fanout_dm_message, forward_dm_federated, wake_dm_ai
from app.chat.group_delivery import (
    fanout_group_message,
    forward_group_message_federated,
    maybe_vectorize_group_message,
    message_view,
    wake_group_ai,
)

logger = logging.getLogger(__name__)

router = APIRouter()


manager = ConnectionManager()
chat_api.set_manager(manager)


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket, token: str = Query(...)):
    """WebSocket 端点：/ws?token=JWT"""

    # 必须先 accept，再验证 token——否则浏览器报 "closed before established"
    await ws.accept()

    payload_result = decode_access_token(token)
    if payload_result.is_err():
        await ws.close(code=4001, reason=payload_result.error)
        return
    payload = payload_result.ok

    user_id = int(payload.get("user_id", 0))
    username = payload.get("username", "unknown")

    if user_id == 0:
        await ws.close(code=4001, reason="令牌数据不完整")
        return
    current_group_id: int | None = None
    current_session_id: str | None = None  # DM 会话 ID 追踪

    # 记录 WebSocket 连接活动（在线追踪兜底）
    from app.services.infrastructure.online_tracker import record_ws_activity
    record_ws_activity(user_id)

    # 缓存用户信息（打字状态/在线需要头像）
    _user_avatar = None
    try:
        from app.database import async_session as _init_db
        async with _init_db() as _init_session:
            from app.models.user import User as UserModel
            _u = (await _init_session.execute(select(UserModel.avatar_url).where(UserModel.id == user_id))).scalar()
            if _u:
                _user_avatar = _u
    except Exception:
        pass

    # WebSocket 连接成功 → 标记为当前在线
    try:
        async with async_session() as _online_db:
            await _online_db.execute(
                sa_update(UserModel).where(UserModel.id == user_id).values(last_active_at=None)
            )
            await _online_db.commit()
    except Exception:
        pass

    # 启动心跳检测
    heartbeat_task = manager.start_heartbeat(ws, user_id)

    try:
        while True:
            raw = await ws.receive_text()
            manager.record_activity(user_id)
            record_ws_activity(user_id)

            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send_json(build_ws_error("INVALID_JSON", "无效的 JSON 格式"))
                continue

            msg_type = data.get("type", "")

            # ---- 订阅（群聊或私信） ----
            if msg_type == "subscribe":
                group_id = data.get("group_id")
                session_id = data.get("session_id")

                # 向后兼容：group_id → 群聊，session_id → 私信
                if group_id is not None:
                    conversation_type = "group"
                elif session_id is not None:
                    conversation_type = "dm"
                else:
                    await ws.send_json(build_ws_error("MISSING_GROUP", "缺少 group_id 或 session_id"))
                    continue

                # 断开旧连接（只摘这一条 socket：同一个人可能还有常驻通知连接/另一个标签页）
                if current_group_id is not None:
                    manager.disconnect(ws, current_group_id, user_id)
                if current_session_id is not None:
                    manager.disconnect_dm(ws, current_session_id, user_id)

                if conversation_type == "group":
                    # 群订阅：校验成员身份（与 DM 分支同口径，防止越权订阅任意群直播）
                    async with async_session() as verify_db:
                        if not await is_group_member(verify_db, group_id, "human", user_id):
                            await ws.send_json(build_ws_error("FORBIDDEN", "你不是该群成员"))
                            continue
                    await manager.connect(ws, group_id, user_id)
                    current_group_id = group_id
                    await ws.send_json({
                        "type": "subscribed",
                        "conversation_type": "group",
                        "data": {"group_id": group_id},
                    })
                    await manager.broadcast_to_group(
                        group_id,
                        {"type": "user_online", "conversation_type": "group", "data": {"user_id": user_id, "username": username}},
                        exclude_user_id=user_id,
                    )
                else:
                    # DM 订阅
                    # 验证用户是此会话的参与者
                    from app.models.dm import DMSession
                    from sqlalchemy import select as sa_select
                    async with async_session() as verify_db:
                        sess_result = await verify_db.execute(
                            sa_select(DMSession).where(DMSession.session_id == session_id)
                        )
                        dm_session = sess_result.scalar_one_or_none()
                        if dm_session is None:
                            await ws.send_json(build_ws_error("INVALID_SESSION", "私信会话不存在"))
                            continue
                        if user_id not in (dm_session.user1_id, dm_session.user2_id):
                            await ws.send_json(build_ws_error("FORBIDDEN", "无权访问此私信会话"))
                            continue

                    current_session_id = session_id
                    await manager.connect_dm(ws, session_id, user_id)
                    await ws.send_json({
                        "type": "subscribed",
                        "conversation_type": "dm",
                        "data": {"session_id": session_id},
                    })
                    # 真人上线 → 通知 DM 对方更新状态点
                    await manager.broadcast_to_dm(
                        session_id,
                        {"type": "state_change", "data": {
                            "user_id": user_id,
                            "state": "active",
                            "last_active_at": None,
                        }},
                        exclude_user_id=user_id,
                    )

            # ---- 常驻通知订阅（站内弹窗） ----
            # 不占用"当前会话订阅位"：同一条连接只收弹窗，不影响 ChatView 的会话推送
            elif msg_type == "notifications_subscribe":
                try:
                    from app.models.dm import DMSession
                    from sqlalchemy import or_ as sa_or

                    async with async_session() as sub_db:
                        # 我所在的群（一次查询；不逐个群验权，避免 N 次往返）
                        group_ids = [
                            row[0] for row in (await sub_db.execute(
                                select(GroupMemberModel.group_id).where(
                                    GroupMemberModel.member_type == "human",
                                    GroupMemberModel.member_id == user_id,
                                )
                            )).all()
                        ]
                        # 我参与的私信（按最近消息排序截断：几百个考古会话不值得挂通知）
                        session_ids = [
                            row[0] for row in (await sub_db.execute(
                                select(DMSession.session_id).where(
                                    sa_or(DMSession.user1_id == user_id, DMSession.user2_id == user_id)
                                ).order_by(DMSession.last_message_at.desc().nullslast())
                                .limit(NOTIFICATION_SESSION_LIMIT)
                            )).all()
                        ]
                    await manager.subscribe_notifications(ws, user_id, group_ids, session_ids)
                    await ws.send_json({
                        "type": "notifications_subscribed",
                        "data": {"groups": len(group_ids), "sessions": len(session_ids)},
                    })
                except Exception as e:
                    logger.warning(f"常驻通知订阅失败（用户 {user_id}）：{e}", exc_info=True)
                    await ws.send_json(build_ws_error("SUBSCRIBE_FAILED", "通知订阅失败"))

            # ---- 发送消息（群聊或私信） ----
            elif msg_type == "send":
                session_id = data.get("session_id")
                group_id = data.get("group_id", current_group_id)
                content = data.get("content", "")
                reply_to = data.get("reply_to")
                sender_type = data.get("sender_type", "human")

                # 判断会话类型
                if session_id:
                    # ── 私信消息 ──
                    dm_attachments = data.get("attachments")
                    if not content and not dm_attachments:
                        await ws.send_json(build_ws_error("MISSING_FIELD", "缺少 content"))
                        continue

                    async with async_session() as db:
                        try:
                            from app.chat.dm import send_dm_message as send_dm_msg, is_user_in_dm_dnd
                            msg = await send_dm_msg(
                                db, session_id, sender_id=user_id,
                                content=content, reply_to=reply_to,
                                attachments=dm_attachments,
                            )
                            await db.commit()
                        except ValueError as e:
                            await ws.send_json(build_ws_error("SEND_FAILED", str(e)))
                            continue
                        except Exception as e:
                            logger.error(f"DM 消息持久化失败: {e}")
                            await ws.send_json(build_ws_error("SEND_FAILED", "消息发送失败"))
                            continue

                    msg["conversation_type"] = "dm"
                    # 审计日志：用户发送消息（fire-and-forget）
                    asyncio.create_task(_log_message_audit(user_id, "dm", session_id, msg["id"]))
                    # 回显给发送者
                    await ws.send_json({"type": "message", "conversation_type": "dm", "data": msg})
                    # 推给会话另一端 + 唤醒 AI + 联邦转发（与外部通道共用同一条分发）
                    await fanout_dm_message(session_id, msg, user_id)
                    wake_dm_ai(session_id, msg, sender_id=user_id, sender_type=sender_type)
                    await forward_dm_federated(session_id, msg)

                else:
                    # ── 群聊消息（原有逻辑） ──
                    attachments = data.get("attachments")
                    if not group_id or (not content and not attachments):
                        await ws.send_json(build_ws_error("MISSING_FIELD", "缺少 group_id 或 content"))
                        continue

                    async with async_session() as db:
                        try:
                            message = await send_gm_message(
                                db, group_id=group_id, sender_type=sender_type,
                                sender_id=user_id, content=content, reply_to=reply_to,
                                attachments=attachments,
                            )
                            await db.flush()
                        except Exception as e:
                            logger.error(f"消息持久化失败: {e}")
                            await ws.send_json(build_ws_error("SEND_FAILED", "消息发送失败"))
                            continue

                        # 统一走 users 表；视图构建收在 chat.group_delivery（外部通道复用同一条）
                        msg_data = await message_view(db, message, sender_name=username)

                        # 审计日志：用户发送消息（fire-and-forget）
                        asyncio.create_task(_log_message_audit(user_id, "group", group_id, message.id))

                        # 先回显给发送者
                        await ws.send_json({"type": "message", "conversation_type": "group", "data": msg_data})

                        # 群消息投递：以「AI 成员」为准（判定收在 delivery_decision 一处）
                        await fanout_group_message(db, group_id, message, content, msg_data)
                        # 持久化提交
                        try:
                            await db.commit()
                        except Exception as e:
                            logger.error(f"消息提交失败: {e}", exc_info=True)
                            await ws.send_json(build_ws_error("SEND_FAILED", "消息提交失败"))
                            continue

                        # 联邦通信：异步转发到共享此群的对等端
                        await forward_group_message_federated(group_id, msg_data, db)
                        # 触发 AI 自动回复 worker（仅人类消息；网页端与外部通道同一条路径）
                        wake_group_ai(group_id, message, content)
                        # 触发向量化 pipeline（仅向量加速群聊）
                        await maybe_vectorize_group_message(db, group_id, message)

            # ---- 输入状态 ----
            elif msg_type == "typing":
                session_id = data.get("session_id")
                group_id = data.get("group_id", current_group_id)
                is_typing = data.get("is_typing", False)

                # 使用连接时缓存的头像
                sender_avatar_url = _user_avatar

                if session_id:
                    # DM 输入状态
                    await manager.broadcast_to_dm(
                        session_id,
                        {
                            "type": "typing",
                            "conversation_type": "dm",
                            "data": {
                                "session_id": session_id,
                                "sender_id": user_id,
                                "username": username,
                                "avatar_url": sender_avatar_url,
                                "is_typing": is_typing,
                            },
                        },
                        exclude_user_id=user_id,
                    )
                elif group_id:
                    await manager.broadcast_to_group(
                        group_id,
                        {
                            "type": "typing",
                            "conversation_type": "group",
                            "data": {
                                "group_id": group_id,
                                "sender_id": user_id,
                                "username": username,
                                "avatar_url": sender_avatar_url,
                                "is_typing": is_typing,
                            },
                        },
                        exclude_user_id=user_id,
                    )

            # ---- pong（心跳响应）— 静默忽略，_last_activity 已更新 ----
            elif msg_type == "pong":
                pass

            # ---- 未知类型 ----
            else:
                logger.debug(f"未知消息类型: {msg_type}")
                # 不报错，静默忽略（允许客户端扩展协议）

    except WebSocketDisconnect:
        logger.info(f"用户 {user_id} WebSocket 断开")
    finally:
        heartbeat_task.cancel()
        # 记录离线时间
        try:
            async with async_session() as _offline_db:
                # 心跳首次检测到离线的时间（一个周期无回应）；正常断开则为 func.now()
                ts = manager.get_offline_timestamp(user_id) or func.now()
                await _offline_db.execute(
                    sa_update(UserModel).where(UserModel.id == user_id).values(last_active_at=ts)
                )
                await _offline_db.commit()
        except Exception:
            pass
        if current_group_id is not None:
            manager.disconnect(ws, current_group_id, user_id)
            await manager.broadcast_to_group(
                current_group_id,
                {"type": "user_offline", "conversation_type": "group", "data": {"user_id": user_id, "username": username}},
            )
        if current_session_id is not None:
            # 真人下线 → 通知 DM 对方更新状态点
            await manager.broadcast_to_dm(
                current_session_id,
                {"type": "state_change", "data": {
                    "user_id": user_id,
                    "state": "inactive",
                    "last_active_at": None,
                }},
                exclude_user_id=user_id,
            )
            manager.disconnect_dm(ws, current_session_id, user_id)
        # 兜底：常驻通知连接、以及切过订阅后残留在别的群/私信池里的这条 socket
        manager.disconnect_all(ws, user_id)

async def _log_message_audit(user_id: int, conv_type: str, conv_id: int | str, message_id: int):
    """审计日志：用户发送消息（只记 message_id，内容查消息表）"""
    try:
        from app.database import async_session
        from app.repositories.audit_repo import SQLAlchemyAuditRepository
        from app.services.audit_service import log_user_action
        from app.utils.auth import get_current_request_ip
        async with async_session() as session:
            await log_user_action(
                SQLAlchemyAuditRepository(session), "send_message", user_id, conv_type,
                target_id=conv_id if isinstance(conv_id, int) else 0,
                details={"message_id": message_id},
                ip=get_current_request_ip(),
            )
    except Exception:
        pass
