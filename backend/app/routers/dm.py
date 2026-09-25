"""
私信（DM）路由
"""
import logging
from fastapi import APIRouter, Depends, HTTPException, status, Query

logger = logging.getLogger(__name__)
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.repositories.export_repo import ExportRepository
from app.routers.deps import get_export_repo
from app.utils.auth import get_current_user
from app.chat.dm import (
    get_or_create_dm_session,
    list_dm_sessions,
    get_dm_session,
    get_dm_messages,
    send_dm_message,
    set_dm_dnd,
    cancel_dm_dnd,
)

router = APIRouter(tags=["私信"])


@router.post("/dm/{target_user_id}")
async def create_or_get_dm(
    target_user_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取或创建与目标用户的私信会话"""
    try:
        return await get_or_create_dm_session(
            db,
            current_user_id=current_user["user_id"],
            target_user_id=target_user_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/dm/sessions")
async def list_my_dm_sessions(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取当前用户的所有私信会话列表（含置顶/特别关心信息）"""
    from app.models.user_preferences import UserDMPreference
    from sqlalchemy import select

    sessions = await list_dm_sessions(db, user_id=current_user["user_id"])

    # 查当前用户的置顶偏好
    pref_result = await db.execute(
        select(UserDMPreference).where(
            UserDMPreference.user_id == current_user["user_id"]
        )
    )
    prefs = {p.session_id: p for p in pref_result.scalars().all()}

    for s in sessions:
        p = prefs.get(s["session_id"])
        s["is_pinned"] = p.is_pinned if p else False
        s["is_special_care"] = p.is_special_care if p else False

    # 置顶优先，然后按最后消息时间降序
    def sort_key(s):
        t = 0
        if s.get("last_message_at"):
            try:
                t = datetime.fromisoformat(s["last_message_at"]).timestamp()
            except Exception:
                t = 0
        return (0 if s.get("is_pinned") else 1, -t)

    sessions.sort(key=sort_key)
    return sessions


@router.post("/dm/{session_id}/pin")
async def pin_dm(
    session_id: str,
    body: dict,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """置顶/取消置顶私信会话（含特别关心）"""
    from app.models.user_preferences import UserDMPreference
    from sqlalchemy import select
    result = await db.execute(select(UserDMPreference).where(
        UserDMPreference.user_id == current_user["user_id"],
        UserDMPreference.session_id == session_id,
    ))
    pref = result.scalar_one_or_none()
    if pref:
        pref.is_pinned = body.get("is_pinned", True)
        if "is_special_care" in body:
            pref.is_special_care = body["is_special_care"]
    else:
        pref = UserDMPreference(
            user_id=current_user["user_id"],
            session_id=session_id,
            is_pinned=body.get("is_pinned", True),
            is_special_care=body.get("is_special_care", False),
        )
        db.add(pref)
    await db.commit()
    return {"is_pinned": pref.is_pinned, "is_special_care": pref.is_special_care}


@router.get("/dm/{session_id}")
async def get_dm_detail(
    session_id: str,
    limit: int = Query(50, ge=1, le=200),
    summary: bool = Query(False, description="仅返回会话摘要（不含消息），供快速切换会话时使用"),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取私信会话详情（summary=true 时仅元数据，不含消息）"""
    try:
        return await get_dm_session(
            db, session_id, current_user["user_id"], message_limit=limit, summary=summary,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/dm/{session_id}/messages")
async def get_dm_message_list(
    session_id: str,
    limit: int = Query(50, ge=1, le=200),
    before_id: int | None = Query(None),
    after_id: int | None = Query(None),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取私信消息列表（游标分页，自动标记已读）"""
    try:
        return await get_dm_messages(
            db, session_id, current_user["user_id"],
            limit=limit, before_id=before_id, after_id=after_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/dm/{session_id}/messages", status_code=status.HTTP_201_CREATED)
async def send_dm(
    session_id: str,
    body: dict,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """发送私信消息"""
    try:
        msg = await send_dm_message(
            db, session_id,
            sender_id=current_user["user_id"],
            content=body.get("content", ""),
            reply_to=body.get("reply_to"),
            attachments=body.get("attachments"),
        )
        # 推给对方：人类发的私信以前**没有任何 WebSocket 推送**，对方只能等轮询
        #（AI 回复走 agent_service/response_worker 自己的 broadcast，人类这条要在这里补）
        try:
            from app.routers.ws import manager
            await manager.broadcast_to_dm(
                session_id,
                {"type": "message", "conversation_type": "dm", "data": msg},
                exclude_user_id=current_user["user_id"],
            )
        except Exception as e:
            logger.warning(f"私信推送失败（非致命）: {e}")

        # 触发 AI 回复（如果对方是 AI）
        await _maybe_trigger_dm_ai_reply(
            db, session_id, msg, current_user["user_id"],
            sender_name=current_user.get("username"),
        )
        return msg
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.exception(f"send_dm 失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/dm/{session_id}/my-token-usage")
async def get_my_token_usage(
    session_id: str,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取当前用户在此 DM 中的 token 消耗（自费聊天时显示）"""
    from app.services.content.conversation_log_service import get_session_token_usage
    usage = await get_session_token_usage(db, session_id, current_user["user_id"])
    return usage


@router.get("/dm/{session_id}/activity")
async def get_dm_activity(
    session_id: str,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取私信当前 AI 思考状态（用于进入对话时恢复活动指示器）"""
    from app.ai.response_worker import get_thinking_state
    return get_thinking_state(f"dm:{session_id}")


@router.post("/dm/{session_id}/dnd")
async def set_dm_dnd_endpoint(
    session_id: str,
    body: dict,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """设置私信免打扰（body: { duration_minutes: int | null }）"""
    try:
        return await set_dm_dnd(
            db, session_id, current_user["user_id"],
            duration_minutes=body.get("duration_minutes"),
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/dm/{session_id}/export")
async def export_dm_chat(
    session_id: str,
    fmt: str = Query("json", pattern="^(json|txt|html)$"),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    export_repo: ExportRepository = Depends(get_export_repo),
):
    """导出私信记录（json / txt / html）"""
    from app.chat.dm import get_dm_session as _get_dm
    from app.services.content.export_service import export_dm_chat_history

    # 校验用户是参与者
    try:
        await _get_dm(db, session_id, current_user["user_id"], message_limit=1)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))

    try:
        content, media_type, filename = await export_dm_chat_history(
            export_repo, session_id, fmt, date_from, date_to
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@router.post("/dm/{session_id}/dnd/cancel")
async def cancel_dm_dnd_endpoint(
    session_id: str,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """取消私信免打扰"""
    try:
        return await cancel_dm_dnd(db, session_id, current_user["user_id"])
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


# ──────────────────────────── 余额弹窗重试 ────────────────────────────

class ContinueWithOwnKeyRequest(BaseModel):
    session_id: str


@router.post("/continue-with-own-key")
async def continue_with_own_key(
    req: ContinueWithOwnKeyRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """用户确认使用自有 API Key 后重新触发 AI 回复"""
    from sqlalchemy import select as sa_select, desc
    from app.models.dm import DMSession
    from app.models.message import Message

    # 验证会话存在且用户是参与者
    sess_result = await db.execute(
        sa_select(DMSession).where(DMSession.session_id == req.session_id)
    )
    session = sess_result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if current_user["user_id"] not in (session.user1_id, session.user2_id):
        raise HTTPException(status_code=403, detail="无权操作此会话")

    # 获取最后一条消息
    msg_result = await db.execute(
        sa_select(Message)
        .where(Message.session_id == req.session_id)
        .order_by(desc(Message.created_at))
        .limit(1)
    )
    msg_row = msg_result.scalar_one_or_none()
    if msg_row is None:
        raise HTTPException(status_code=404, detail="消息不存在")

    # 重新触发 AI 回复，使用自有 Key
    await _maybe_trigger_dm_ai_reply(
        db, req.session_id,
        {"id": msg_row.id, "content": msg_row.content},
        current_user["user_id"],
        force_own_key=True,
    )
    return {"ok": True}


# ============================================================
# 内部：AI 回复触发
# ============================================================

async def _maybe_trigger_dm_ai_reply(
    db: AsyncSession,
    session_id: str,
    msg: dict,
    sender_id: int,
    force_own_key: bool = False,
    sender_name: str | None = None,
):
    """如果消息的接收方是 AI，触发 AI 自动回复"""
    import logging
    logger = logging.getLogger(__name__)
    from sqlalchemy import select
    from app.models.dm import DMSession
    from app.models.user import User

    # 找到接收方
    result = await db.execute(
        select(DMSession).where(DMSession.session_id == session_id)
    )
    session = result.scalar_one_or_none()
    if session is None:
        return

    receiver_id = session.user2_id if session.user1_id == sender_id else session.user1_id

    # 检查接收方是否是 AI
    user_result = await db.execute(
        select(User).where(User.id == receiver_id, User.type == "ai")
    )
    ai_user = user_result.scalar_one_or_none()
    if ai_user is None:
        return

    # 找到对应的 agent
    from app.models.agent import Agent
    agent_result = await db.execute(
        select(Agent).where(Agent.user_id == receiver_id)
    )
    agent = agent_result.scalar_one_or_none()
    if agent is None:
        return

    # ── 2026-08-09: AI↔AI 私信限额 ──
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from app.config import settings
    from app.services.social import dm_quota
    now = datetime.now(ZoneInfo(settings.display_timezone))

    # 创建者发消息 → 重置该 AI 的 creator_chat 计数
    if sender_id == agent.owner_id:
        dm_quota.reset_by_creator(agent, now)

    # 发送方是 AI → AI↔AI 场景：双向限额检查，任一超限则不触发（消息已入库，AI 下次打开会话能看到）
    sender_is_ai = await db.scalar(
        select(User.id).where(User.id == sender_id, User.type == "ai")
    )
    if sender_is_ai:
        sender_agent = await db.scalar(
            select(Agent).where(Agent.user_id == sender_id)
        )
        if sender_agent is None:
            return
        if not dm_quota.quota_allows(sender_agent, "send", now) or not dm_quota.quota_allows(agent, "receive", now):
            return
        dm_quota.consume(sender_agent, "send", now)
        dm_quota.consume(agent, "receive", now)
        await db.flush()

    # ── v0.1.8: 对话权限与限额决策 ──
    is_owner = sender_id == agent.owner_id

    if not is_owner:
        # 检查是否允许他人对话
        if not agent.allow_others_chat:
            # 禁止模式
            if agent.disallow_mode == "own_key" and not force_own_key:
                # 检查聊天者是否有自有 Key
                chatter_result = await db.execute(
                    select(User).where(User.id == sender_id)
                )
                chatter = chatter_result.scalar_one_or_none()
                if chatter and chatter.api_key_encrypted:
                    force_own_key = True  # 允许，走自有 Key
                else:
                    # 聊天者无自有 Key → 发系统 DM 提示
                    await _send_system_dm_notice(
                        db, session_id, agent,
                        "此 AI 需要你使用自有 API Key 才能对话，请在设置中配置 API Key。"
                    )
                    return
            elif agent.disallow_mode == "strict":
                return  # 严格禁止，静默跳过
        else:
            # 允许模式 → 检查配额
            if agent.others_chat_mode == "quota":
                used = agent.others_chat_used or 0
                quota = agent.others_chat_quota or 30
                if used >= quota:
                    # 配额耗尽 → 自动关闭 + 通知主人
                    agent.allow_others_chat = False
                    await db.flush()
                    await _notify_owner_quota_exhausted(db, agent, used, quota)
                    # 进入禁止分支
                    if agent.disallow_mode == "own_key":
                        chatter_result = await db.execute(
                            select(User).where(User.id == sender_id)
                        )
                        chatter = chatter_result.scalar_one_or_none()
                        if chatter and chatter.api_key_encrypted:
                            force_own_key = True
                        else:
                            await _send_system_dm_notice(
                                db, session_id, agent,
                                "此 AI 的对话配额已用完，且需要你使用自有 API Key，请在设置中配置。"
                            )
                            return
                    else:
                        return  # strict
                else:
                    # 配额未满 → 计数 +1
                    agent.others_chat_used = used + 1
                    await db.flush()

    # ── v0.1.8: 余额检查（通用/半通用 AI，聊天者要付）──
    if not is_owner and not force_own_key and agent.ai_type in ("general", "semi_general"):
        chatter_result = await db.execute(
            select(User).where(User.id == sender_id)
        )
        chatter = chatter_result.scalar_one_or_none()
        if chatter:
            chatter_eff = max(0, (chatter.platform_gifted_credit or 0)) + (chatter.api_credit or 0)
            if chatter_eff <= 0:
                if chatter.api_key_encrypted:
                    # 有自有 Key → WebSocket 弹窗询问
                    from app.routers.ws import manager
                    await manager.send_to_user(sender_id, {
                        "type": "balance_prompt",
                        "data": {
                            "agent_id": agent.id,
                            "agent_name": agent.name,
                            "session_id": session_id,
                        }
                    })
                    return  # 不入队，等用户确认
                else:
                    # 没余额也没 Key → 系统提示
                    await _send_system_dm_notice(
                        db, session_id, agent,
                        "你的额度不足，请在设置中配置 API Key 或联系管理员充值。"
                    )
                    return

    # ── v1.1.0: 自动重置配额 ──
    if getattr(agent, 'auto_reset_quota', False) and not is_owner:
        agent.others_chat_used = 0
        await db.flush()

    # ── 忙时中断注入：AI 正在执行，直接注入当前 tool loop ──
    from app.ai.executor import add_pending_interrupt, is_agent_running
    if await is_agent_running(agent.id):
        await add_pending_interrupt(agent.id, {
            "type": "user_message",
            "content": msg["content"],
            "message_id": msg["id"],
            "session_id": session_id,
            "sender_id": sender_id,
            "sender_name": sender_name,
        })
        logger.info(f"AI {agent.name}({agent.id}) 正忙，DM 中断消息已注入")
    else:
        # ── 推入 AI 回复队列 ──
        from app.ai.response_worker import message_queue
        import asyncio
        try:
            message_queue.put_nowait({
                "conversation_type": "dm",
                "session_id": session_id,
                "message_id": msg["id"],
                "content": msg["content"],
                "sender_type": "human" if sender_id != receiver_id else "ai",
                "sender_id": sender_id,
                "chain_depth": 0,
                "force_own_key": force_own_key,  # v0.1.8
            })
        except asyncio.QueueFull:
            logger.warning("AI 回复队列已满，丢弃 DM 事件")


async def _send_system_dm_notice(db: AsyncSession, session_id: str, agent, text: str):
    """向 DM 会话发送系统提示消息"""
    from app.chat.dm import send_dm_message
    from app.models.user import User as UserModel
    try:
        # 获取系统用户
        sys_result = await db.execute(
            select(UserModel).where(UserModel.id == 0)
        )
        sys_user = sys_result.scalar_one_or_none()
        if sys_user is None:
            return
        # 以系统用户身份发消息
        await send_dm_message(
            db, session_id=session_id,
            sender_id=0, sender_type="system",
            content=f"🤖 {agent.name}：{text}",
        )
    except Exception:
        pass


async def _notify_owner_quota_exhausted(db: AsyncSession, agent, used: int, quota: int):
    """通知 AI 主人：他人对话配额已用完"""
    from app.chat.dm import get_or_create_dm_session, send_dm_message
    from app.models.user import User as UserModel
    try:
        owner_result = await db.execute(
            select(UserModel).where(UserModel.id == agent.owner_id)
        )
        owner = owner_result.scalar_one_or_none()
        if owner is None:
            return
        # 获取或创建 DM 会话
        dm = await get_or_create_dm_session(db, agent.user_id, agent.owner_id)
        await send_dm_message(
            db, session_id=dm["session_id"],
            sender_id=0, sender_type="system",
            content=(
                f"📊 你的 AI「{agent.name}」已被其他人触发 {used} 次对话，"
                f"已达到配额上限（{quota} 条），已自动关闭「允许他人对话」。"
                f"你可以在 AI 设置中重置计数或调整配额。"
            ),
        )
    except Exception:
        pass
