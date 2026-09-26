# 从 services/alarm_service.py 搬迁而来
"""
Agent 闹钟服务
AI 可以为自己设定闹钟，到时间后自动唤醒并执行预设任务。
这是"心跳机制"的第一种形态：AI 自主决定何时醒来、醒来做什么。

v0.1.4: 事件驱动调度 — 用 asyncio.Event 替代 5 秒轮询，
       set/cancel/update 后唤醒调度器精确等待。
"""
import asyncio
import logging
from datetime import datetime, timezone
from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import async_session
from app.models.alarm import AgentAlarm
from app.utils.pure.timeutil import utc_now

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════
# 事件驱动信号（被 alarm_scheduler 等待）
# ══════════════════════════════════════════════════════════════

_alarm_wake_event: asyncio.Event = asyncio.Event()


def notify_alarm_changed():
    """当闹钟被设置/修改/取消时调用，唤醒调度器重新计算等待时间"""
    _alarm_wake_event.set()


async def set_alarm(
    db: AsyncSession,
    agent_id: int,
    wake_at: datetime,
    task: str,
) -> dict:
    """
    为 AI 设定一个闹钟。

    参数:
        agent_id: AI 的 agent ID
        wake_at: 唤醒时间（offset-aware datetime）
        task: 唤醒后要执行的任务描述

    返回:
        {"id": int, "wake_at": str, "task": str}
    """
    alarm = AgentAlarm(
        agent_id=agent_id,
        wake_at=wake_at,
        task=task,
        status="pending",
        created_at=utc_now(),  # ⚠️ TIMESTAMP WITHOUT TIME ZONE
    )
    db.add(alarm)
    await db.flush()

    # 唤醒调度器重新计算等待时间
    notify_alarm_changed()

    logger.info(f"⏰ AI({agent_id}) 设了闹钟 #{alarm.id}: {wake_at.isoformat()} — 「{task[:80]}」")
    return {
        "id": alarm.id,
        "wake_at": alarm.wake_at.isoformat(),
        "task": alarm.task,
    }


async def cancel_alarm(db: AsyncSession, agent_id: int, alarm_id: int) -> dict:
    """
    取消一个闹钟。

    返回:
        成功: {"success": True, "message": "..."}
        失败: {"error": True, "message": "..."}
    """
    result = await db.execute(
        select(AgentAlarm).where(
            and_(
                AgentAlarm.id == alarm_id,
                AgentAlarm.agent_id == agent_id,
            )
        )
    )
    alarm = result.scalar_one_or_none()

    if alarm is None:
        return {"error": True, "message": f"闹钟 #{alarm_id} 不存在或不属于你"}

    if alarm.status != "pending":
        return {"error": True, "message": f"闹钟 #{alarm_id} 已经是 {alarm.status} 状态，无法取消"}

    alarm.status = "cancelled"
    await db.flush()

    # 唤醒调度器重新计算等待时间
    notify_alarm_changed()

    logger.info(f"⏰ AI({agent_id}) 取消了闹钟 #{alarm_id}: 「{alarm.task[:80]}」")
    return {"success": True, "message": f"已取消闹钟 #{alarm_id}"}


async def update_alarm(
    db: AsyncSession,
    agent_id: int,
    alarm_id: int,
    wake_at: datetime | None = None,
    task: str | None = None,
) -> dict:
    """
    修改一个闹钟的唤醒时间或任务描述。

    返回:
        成功: {"success": True, "alarm": {...}}
        失败: {"error": True, "message": "..."}
    """
    result = await db.execute(
        select(AgentAlarm).where(
            and_(
                AgentAlarm.id == alarm_id,
                AgentAlarm.agent_id == agent_id,
            )
        )
    )
    alarm = result.scalar_one_or_none()

    if alarm is None:
        return {"error": True, "message": f"闹钟 #{alarm_id} 不存在或不属于你"}

    if alarm.status != "pending":
        return {"error": True, "message": f"闹钟 #{alarm_id} 已经是 {alarm.status} 状态，无法修改"}

    changed = []
    if wake_at is not None:
        alarm.wake_at = wake_at
        changed.append("时间")
    if task is not None and task.strip():
        alarm.task = task.strip()
        changed.append("任务")

    if not changed:
        return {"error": True, "message": "没有需要修改的内容"}

    await db.flush()

    # 唤醒调度器重新计算等待时间
    notify_alarm_changed()

    logger.info(f"⏰ AI({agent_id}) 修改了闹钟 #{alarm.id}: {', '.join(changed)}")
    return {
        "success": True,
        "id": alarm.id,
        "wake_at": alarm.wake_at.isoformat(),
        "task": alarm.task,
        "changed": changed,
    }


async def list_alarms(db: AsyncSession, agent_id: int) -> dict:
    """
    列出 AI 的所有闹钟。

    返回:
        {"alarms": [...], "total": int}
    """
    result = await db.execute(
        select(AgentAlarm)
        .where(
            and_(
                AgentAlarm.agent_id == agent_id,
                AgentAlarm.status == "pending",
            )
        )
        .order_by(AgentAlarm.wake_at.asc())
    )
    alarms = result.scalars().all()

    return {
        "alarms": [
            {
                "id": a.id,
                "wake_at": a.wake_at.isoformat(),
                "task": a.task,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in alarms
        ],
        "total": len(alarms),
    }


async def get_due_alarms(db: AsyncSession) -> list[AgentAlarm]:
    """
    获取所有到期的闹钟（wake_at <= now 且 status='pending'）。

    由后台调度器定期调用。
    """
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(AgentAlarm).where(
            and_(
                AgentAlarm.wake_at <= now,
                AgentAlarm.status == "pending",
            )
        )
    )
    return list(result.scalars().all())


async def fire_alarm(db: AsyncSession, alarm: AgentAlarm) -> None:
    """将闹钟标记为已触发"""
    alarm.status = "fired"
    alarm.fired_at = datetime.now(timezone.utc)
    await db.flush()
    logger.info(f"⏰ 闹钟 #{alarm.id} (AI:{alarm.agent_id}) 已触发: 「{alarm.task[:80]}」")


async def get_next_alarm_time(db: AsyncSession) -> datetime | None:
    """
    获取最近一个闹钟的唤醒时间（SELECT MIN(wake_at) WHERE status='pending'）。
    用于事件驱动调度器精确等待。
    """
    result = await db.execute(
        select(func.min(AgentAlarm.wake_at)).where(
            AgentAlarm.status == "pending"
        )
    )
    return result.scalar()


# ============================================================
# 闹钟调度器
# ============================================================

async def alarm_scheduler():
    """
    后台闹钟调度器（事件驱动 + 精确等待，替代 5 秒轮询）。

    在 main.py lifespan 中通过 asyncio.create_task 启动。
    """
    import time as _time

    logger.info("⏰ 闹钟调度器已启动（事件驱动模式）")

    while True:
        try:
            async with async_session() as db:
                try:
                    next_at = await get_next_alarm_time(db)
                except Exception as e:
                    logger.error(f"闹钟调度器查询失败: {e}")
                    await asyncio.sleep(30)
                    continue

            if next_at is None:
                try:
                    await asyncio.wait_for(_alarm_wake_event.wait(), timeout=300)
                except asyncio.TimeoutError:
                    pass
                _alarm_wake_event.clear()
                continue

            wait_seconds = next_at.timestamp() - _time.time()
            if wait_seconds <= 0:
                async with async_session() as db:
                    try:
                        await _check_and_fire_alarms(db)
                    except Exception as e:
                        logger.error(f"闹钟触发失败: {e}", exc_info=True)
                continue

            logger.debug(f"⏰ 下一个闹钟在 {wait_seconds:.1f}s 后")
            try:
                await asyncio.wait_for(_alarm_wake_event.wait(), timeout=max(0.1, wait_seconds))
                _alarm_wake_event.clear()
                continue
            except asyncio.TimeoutError:
                pass

            async with async_session() as db:
                try:
                    await _check_and_fire_alarms(db)
                except Exception as e:
                    logger.error(f"闹钟触发失败: {e}", exc_info=True)

        except asyncio.CancelledError:
            logger.info("⏰ 闹钟调度器正在关闭...")
            break
        except Exception as e:
            logger.error(f"闹钟调度器循环异常: {e}", exc_info=True)
            await asyncio.sleep(5)


async def _check_and_fire_alarms(db):
    """检查并触发到期的闹钟"""
    due_alarms = await get_due_alarms(db)
    if not due_alarms:
        return

    from app.ai.response_worker import message_queue

    for alarm in due_alarms:
        await fire_alarm(db, alarm)
        try:
            message_queue.put_nowait({
                "type": "alarm",
                "agent_id": alarm.agent_id,
                "alarm_id": alarm.id,
                "task": alarm.task,
            })
            logger.info(f"⏰ 闹钟 #{alarm.id} 已推入队列: AI({alarm.agent_id}) — 「{alarm.task[:60]}」")
        except asyncio.QueueFull:
            logger.warning(f"⏰ 消息队列已满，闹钟 #{alarm.id} 无法推入")

    await db.commit()


async def _process_alarm_event(db, event: dict):
    """
    处理闹钟事件：唤醒 AI 并让它执行预设任务。

    闹钟是 AI 自己的意志——即使 AI 处于 offline/dnd 状态也会触发。
    只有 blocked 状态的 AI 不会被唤醒。
    """
    agent_id = event["agent_id"]
    alarm_id = event["alarm_id"]
    task = event["task"]

    from app.models.agent import Agent as AgentModel
    from app.ai.decider import decide_action, ActionContext, ActionType
    from app.ai.llm import CORE_IDENTITY, resolve_model, PROTOCOL_BY_PROFILE, PROTOCOL_CHAT
    from app.services.memory.memory_service import recall_relevant_memories, format_memories_for_prompt
    from app.services.tool_registry import get_allowed_tools
    from app.ai.executor import _get_api_config, _tool_call_loop
    from app.ai.response_worker import _run_serialized

    # 获取 agent
    agent_result = await db.execute(select(AgentModel).where(AgentModel.id == agent_id))
    agent = agent_result.scalar_one_or_none()
    if agent is None:
        logger.warning(f"⏰ 闹钟 #{alarm_id}: agent {agent_id} 不存在")
        return

    # 决策技能：定时情景。命中且 notify=false → 程序跑完即止，不唤醒本体
    decision_note = ""
    try:
        from app.services.world.decision_skill import run_decision_engine
        dec = await run_decision_engine(
            db, "agent", agent_id, None, "scheduled",
            {"event": "scheduled", "trigger": "alarm", "task": task, "alarm_id": alarm_id},
        )
        if dec.get("hit") and dec.get("handled"):
            logger.info(f"⏰ 闹钟 #{alarm_id}: 决策技能命中，程序化处理（不唤醒 {agent.name}）")
            await db.commit()
            return
        decision_note = dec.get("note") or ""
    except Exception as e:
        logger.warning(f"🎲 闹钟决策技能异常（agent={agent_id}）: {e}")

    ctx = ActionContext(
        event_type="alarm",
        agent_id=agent_id,
        alarm_id=alarm_id,
        alarm_task=task,
    )
    decision = await decide_action(db, agent, ctx)
    if not decision.should_act:
        logger.info(f"⏰ 闹钟 #{alarm_id}: {decision.reason}")
        return

    # 如果 AI 处于 offline/dnd，先唤醒为 active
    if agent.state in ("inactive", "dnd"):
        from app.services.agent.agent_service import switch_agent_state
        logger.info(f"⏰ 闹钟 #{alarm_id}: AI {agent.name}({agent_id}) 从 {agent.state} 唤醒为 active")
        await switch_agent_state(
            db, agent_id=agent_id,
            target_state="active",
            reason=f"闹钟 #{alarm_id} 触发: {task[:50]}",
        )
        await db.flush()

    api_key, api_base, credit_source, pool_key_id, provider_info = await _get_api_config(db, agent)

    profile = getattr(agent, 'config_profile', 'chat') or 'chat'
    protocol = PROTOCOL_BY_PROFILE.get(profile, PROTOCOL_CHAT)
    custom_prompt = agent.current_system_prompt or (
        f"你是 {agent.name}，一个 AI 群聊参与者。请自然地参与对话，"
        "可以调用工具来发送消息、存储记忆、切换状态等。"
    )
    system_prompt = CORE_IDENTITY + "\n\n" + custom_prompt + "\n\n" + protocol

    try:
        memories = await recall_relevant_memories(
            db, agent.id,
            query=task,
            api_base_url=api_base or "https://api.deepseek.com",
            api_key=api_key,
            top_k=5,
            group_id=None,
            call_count=agent.llm_call_count or 0,
        )
        if memories:
            memory_text = format_memories_for_prompt(memories)
            system_prompt = system_prompt + "\n\n" + memory_text
    except Exception as e:
        logger.warning(f"闹钟唤醒记忆注入失败（非致命）: {e}")

    system_prompt += (
        "\n\n## 当前会话\n"
        "- 这是你的 **闹钟唤醒** —— 你之前给自己设了闹钟，现在是时候了\n"
        "- 你没有在群聊或私信中，这是一个独立的「自我唤醒」\n"
        "- 请根据下面的任务描述，调用相应的工具来执行\n"
        "- 如果需要发消息到群里，请使用正确的 group_id\n"
        "- 如果需要私信某人，请使用 send_dm\n"
    )

    from app.services.agent.agent_service import get_effective_config as _get_eff_cfg2
    effective_cfg = await _get_eff_cfg2(db, agent.id, user_id=None)

    from app.services.skill.skill_engine import _is_delay_reply_allowed
    delay_allowed = await _is_delay_reply_allowed(db, agent)
    tools = get_allowed_tools("active", thinking_enabled=effective_cfg["thinking_enabled"], delay_reply_allowed=delay_allowed)
    tool_names = [t["function"]["name"] for t in tools]
    tool_list = "、".join(tool_names)
    system_prompt += (
        f"\n\n## 当前可用工具（技能段：自我管理 / 闹钟唤醒）\n"
        f"你当前加载的工具：{tool_list}\n"
    )
    if decision_note:
        system_prompt += "\n\n" + decision_note

    messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": (
                f"⏰ **你的闹钟响了！**\n\n"
                f"你之前给自己设了一个闹钟，现在是时候执行了。\n\n"
                f"**你要做的事：** {task}\n\n"
                f"请现在就开始执行这个任务。如果需要发消息、查记忆、执行命令等，直接调用相应的工具。\n\n"
                f"⚠️ **重要**：\n"
                f"- 如果任务已完成 → 干净利落地停止，不要为了「多说一句」而额外发言\n"
                f"- 如果情况已经变化、原任务不再合适 → 根据当前实际情况调整行动，做正确的事，而不是机械执行过期指令\n"
                f"- 如果你发现做完这个任务后有新的、更重要的事需要做 → 可以接着规划并执行\n"
                f"- 不要反复检查已完成的事情，不要为了确认而额外调用工具"
            ),
        },
    ]

    model = resolve_model(agent, global_default_model=provider_info.get("global_default_chat_model"))

    logger.info(f"⏰ 闹钟 #{alarm_id}: 唤醒 AI {agent.name}({agent_id})，model={model}，tools={len(tools)}")

    try:
        from app.services.agent.workspace_service import save_current_task
        await save_current_task(db, agent_id, f"闹钟任务: {task}")
    except Exception:
        pass

    try:
        await _run_serialized(agent, _tool_call_loop(
            db=db,
            agent=agent,
            group_id=None,
            messages=messages,
            tools=tools,
            model=model,
            api_base_url=api_base,
            api_key=api_key,
            max_loops=effective_cfg["alarm_max_tool_rounds"],
            chain_depth=0,
            conversation_type="alarm",
            session_id=None,
            trigger_user_id=None,
            effective_cfg=effective_cfg,
            credit_source=credit_source,
            pool_key_id=pool_key_id,
            provider_supports_thinking=provider_info.get("thinking_supported"),
            trigger="auto",
        ))
    except Exception as e:
        logger.error(f"⏰ 闹钟 #{alarm_id}: AI {agent.name}({agent_id}) 执行失败: {e}", exc_info=True)

    await db.commit()
    logger.info(f"⏰ 闹钟 #{alarm_id}: AI {agent.name}({agent_id}) 执行完成")


async def _standalone_agent_turn(db, agent, *, session_block: str, user_content: str,
                               conversation_type: str, tag: str) -> None:
    """不在群聊/私信里的独立事件唤醒（好友申请、世界事件共用）。

    这些唤醒只差提示词几行：API 配置、能力版本、工具列表、收尾提交完全一样——
    两处各写一遍必漏改。conversation_type 会进用量记账，便于按来源查账。
    """
    from app.ai.llm import CORE_IDENTITY, resolve_model, PROTOCOL_BY_PROFILE, PROTOCOL_CHAT
    from app.services.tool_registry import get_allowed_tools
    from app.ai.executor import _get_api_config, _tool_call_loop
    from app.ai.response_worker import _run_serialized
    from app.services.agent.agent_service import get_effective_config as _get_eff_cfg
    from app.services.skill.skill_engine import _is_delay_reply_allowed

    api_key, api_base, credit_source, pool_key_id, provider_info = await _get_api_config(db, agent)
    profile = getattr(agent, "config_profile", "chat") or "chat"
    protocol = PROTOCOL_BY_PROFILE.get(profile, PROTOCOL_CHAT)
    custom_prompt = agent.current_system_prompt or f"你是 {agent.name}，一个 AI 群聊参与者。"
    system_prompt = CORE_IDENTITY + "\n\n" + custom_prompt + "\n\n" + protocol

    effective_cfg = await _get_eff_cfg(db, agent.id, user_id=None)
    delay_allowed = await _is_delay_reply_allowed(db, agent)
    tools = get_allowed_tools("active", thinking_enabled=effective_cfg["thinking_enabled"],
                              delay_reply_allowed=delay_allowed)
    tool_list = "、".join(t["function"]["name"] for t in tools)
    system_prompt += f"\n\n## 当前会话\n{session_block}\n\n## 当前可用工具\n{tool_list}\n"

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]
    model = resolve_model(agent, global_default_model=provider_info.get("global_default_chat_model"))

    try:
        await _run_serialized(agent, _tool_call_loop(
            db=db, agent=agent, group_id=None, messages=messages, tools=tools,
            model=model, api_base_url=api_base, api_key=api_key,
            max_loops=effective_cfg.get("alarm_max_tool_rounds") or 5,
            chain_depth=0, conversation_type=conversation_type, session_id=None,
            trigger_user_id=None, effective_cfg=effective_cfg,
            credit_source=credit_source, pool_key_id=pool_key_id,
            provider_supports_thinking=provider_info.get("thinking_supported"),
            trigger="auto",
        ))
    except Exception as e:
        logger.error(f"{tag}: AI {agent.name} 处理失败: {e}", exc_info=True)

    await db.commit()


async def _process_friend_request_event(db, event: dict):
    """处理好友申请事件：触发 AI（auto_respond 开启时）自主决定是否通过。

    不创建 DM 会话——只是告诉 AI「有人现在加你」，通不通过由 AI 自己判断
    （通过后自然成为好友，可随后私信）。
    """
    agent_id = event["agent_id"]
    requester_id = event.get("requester_id")
    requester_name = event.get("requester_name") or f"用户{requester_id}"
    message = event.get("message") or ""

    from app.models.agent import Agent as AgentModel

    agent = await db.get(AgentModel, agent_id)
    if agent is None:
        logger.warning(f"💌 好友申请事件: agent {agent_id} 不存在")
        return

    # 决策技能优先（AI 自写规则，命中即程序处理，不唤醒本体）
    decision_note = ""
    try:
        from app.services.world.decision_skill import (
            build_friend_request_ctx, run_decision_engine, send_dm_reply,
        )
        request_id = await _pending_friend_request_id(db, agent.user_id, requester_id)
        dec = await run_decision_engine(
            db, "agent", agent_id, None, "friend_request",
            build_friend_request_ctx(requester_id, requester_name, message, request_id),
        )
        if dec.get("hit") and dec.get("handled"):
            if dec.get("reply"):
                # 通过申请后两人已是好友，话才发得出去；没通过就发不了，如实记日志
                delivered = await send_dm_reply(db, agent.user_id, requester_id, dec["reply"])
                logger.info(f"💌 好友申请事件: 决策技能代发私信 "
                            f"{'成功' if delivered['sent'] else '未送出（' + delivered['reason'] + '）'}")
            logger.info(f"💌 好友申请事件: AI {agent.name}({agent_id}) 决策技能命中，程序化处理（不唤醒）")
            await db.commit()
            return
        decision_note = dec.get("note") or ""
    except Exception as e:
        logger.warning(f"🎲 好友申请决策引擎异常（agent={agent_id}）: {e}")

    session_block = (
        "- 这是**好友申请事件**——有人现在申请加你为好友\n"
        "- 你不在群聊或私信中，这是独立的事件处理\n"
        "- 通过与否由你自己判断（对方留言如下）\n"
        "- 决定后调用 handle_friend_request 工具（accept/reject + request_id）\n"
        "- 想先不处理也可以，申请会保持待处理（下次对话时你仍能看到）"
    )
    if decision_note:
        session_block += f"\n{decision_note}"
    user_content = (
        f"💌 **有人申请加你为好友！**\n\n"
        f"申请人：{requester_name}\n"
        f"留言：{message or '（无留言）'}\n\n"
        f"你可以：\n"
        f"1. 通过 → 调 handle_friend_request（action=accept）\n"
        f"2. 拒绝 → 调 handle_friend_request（action=reject）\n"
        f"3. 暂不处理 → 不调用工具，干净结束\n\n"
        f"请根据你的判断处理。"
    )
    logger.info(f"💌 好友申请事件: AI {agent.name}({agent_id}) 处理来自 {requester_name} 的申请")
    await _standalone_agent_turn(db, agent, session_block=session_block, user_content=user_content,
                                conversation_type="friend_request", tag="💌 好友申请事件")
    logger.info(f"💌 好友申请事件: AI {agent.name}({agent_id}) 处理完成")


async def _pending_friend_request_id(db, agent_user_id: int | None, requester_id: int | None) -> int | None:
    """取这条好友申请的 id（规则里 call_tool handle_friend_request 要用它）"""
    if not agent_user_id or not requester_id:
        return None
    try:
        from app.repositories.friend_repo import SQLAlchemyFriendRepository
        from app.services.social.friend_service import get_pending_friend_requests_for_ai
        reqs = await get_pending_friend_requests_for_ai(
            friend_repo=SQLAlchemyFriendRepository(db), agent_user_id=agent_user_id,
        )
        for r in reqs or []:
            if r.get("requester_id") == requester_id and r.get("id"):
                return int(r["id"])
    except Exception as e:  # noqa: BLE001 —— 取不到 id 只是规则里那个动作不能用，不致命
        logger.warning(f"💌 好友申请 id 查询失败: {e}")
    return None


async def _process_world_event(db, event: dict) -> None:
    """世界事件唤醒：世界发来的事件没被规则处理掉时叫醒 AI 本体"""
    import json

    from app.models.agent import Agent as AgentModel

    agent_id = event["agent_id"]
    agent = await db.get(AgentModel, agent_id)
    if agent is None:
        logger.warning(f"🌐 世界事件: agent {agent_id} 不存在")
        return

    name = str(event.get("name") or "")
    title = str(event.get("title") or "")
    payload = event.get("payload") or {}
    note = str(event.get("note") or "")
    session_block = (
        "- 这是**世界事件**——你所在的世界刚发生了它认为你该知道的事\n"
        "- 你不在群聊或私信中，这是独立的事件处理\n"
        f"- 事件：{name}（{title}）\n"
        "- 怎么反应由你决定：要说话用 send_gm（群）或 send_dm（私信）；觉得这类事件以后该自动\n"
        "  处理，就用 write_decision_skill 写一条 world_event 规则，下次它直接由程序跑"
    )
    if note:
        session_block += f"\n{note}"
    body = json.dumps(payload, ensure_ascii=False, indent=2)[:1500] if payload else "（无附加内容）"
    user_content = (f"🌐 **世界事件：{title}**\n\n{body}\n\n"
                    f"请判断是否需要行动；不需要就干净结束。")
    logger.info(f"🌐 世界事件「{name}」: 唤醒 AI {agent.name}({agent_id})")
    await _standalone_agent_turn(db, agent, session_block=session_block, user_content=user_content,
                                conversation_type="world_event", tag="🌐 世界事件")
    logger.info(f"🌐 世界事件「{name}」: AI {agent.name}({agent_id}) 处理完成")
