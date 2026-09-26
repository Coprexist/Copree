# 从 services/ai_response_worker.py 搬迁而来
"""
AI 响应 Worker — 极薄大脑

职责：
- 维护全局状态（message_queue / _thinking_state / _agent_locks）
- 信号路由（ai_response_worker → _process_event）
- 编排入口（_maybe_trigger_ai_reply / _trigger_dm_ai_reply）

AI 执行的"肌肉"已抽离到：
  - app.ai.executor:  _tool_call_loop / _get_api_config / _send_system_error / ...
  - app.ai.alarm:     alarm_scheduler / _process_alarm_event / ...
"""
import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone, timedelta
from sqlalchemy import select
from app.database import async_session
from app.models.agent import Agent as AgentModel
from app.models.group import Group, GroupMember
from app.models.user import User
from app.config import settings
from app.chat import chat_api
from app.utils.text import extract_mentions as _extract_mentions, check_mention as _check_mention
from app.utils.crypto import APIKeyDecryptError
from app.ai.executor import _tool_call_loop, _get_api_config, _check_rate_limit, _send_system_error, _send_system_error_notification, add_pending_interrupt, is_agent_running, mark_agent_running, unmark_agent_running
from app.ai.alarm import _process_alarm_event

logger = logging.getLogger(__name__)

# 全局消息队列（ws.py 推送，worker 消费）
message_queue: asyncio.Queue = asyncio.Queue(maxsize=500)

# 速率限制：{agent_id: last_call_timestamp}
_rate_limit_tracker: dict[int, float] = {}

# AI 并发锁：resonance/custom 类型同一 AI 串行执行 LLM 调用，general/semi_general 不锁
_agent_locks: dict[int, asyncio.Lock] = {}

# 思考/输入中状态追踪：{conv_key: {agent_id: {name, avatar_url, state}}}
# conv_key = "group:7" 或 "dm:1_10"
# state = "thinking" | "typing"
_thinking_state: dict[str, dict[int, dict]] = {}


def get_thinking_state(conv_key: str) -> dict[int, dict]:
    """获取指定对话的思考/输入中状态"""
    return _thinking_state.get(conv_key, {})


async def _run_serialized(agent, coro):
    """resonance/custom 类型加锁串行，general/semi_general 直接执行"""
    if agent.ai_type in ("general", "semi_general"):
        return await coro
    lock = _agent_locks.get(agent.id)
    if lock is None:
        lock = asyncio.Lock()
        _agent_locks[agent.id] = lock
    async with lock:
        await mark_agent_running(agent.id)
        try:
            return await coro
        finally:
            await unmark_agent_running(agent.id)


# ============================================================
# 主循环
# ============================================================

async def ai_response_worker():
    """
    后台主循环：持续消费消息队列，为每条消息检查并触发 AI 回复。
    在 main.py lifespan 中通过 asyncio.create_task 启动。
    """
    logger.info("🤖 AI 回复 worker 已启动，等待消息事件...")
    while True:
        try:
            event = await message_queue.get()
            logger.info(f"📬 Worker 收到事件: group={event.get('group_id')}, msg={event.get('message_id')}, queue_remaining={message_queue.qsize()}")
            # v0.1.4: 记录队列深度
            try:
                from app.services.infrastructure.metrics_collector import metrics
                await metrics.record_queue_depth(message_queue.qsize())
            except Exception:
                pass
            async with async_session() as db:
                try:
                    await _process_event(db, event)
                except Exception as e:
                    logger.error(f"处理消息事件失败: {e}", exc_info=True)
            message_queue.task_done()
        except asyncio.CancelledError:
            logger.info("AI 回复 worker 正在关闭...")
            break
        except Exception as e:
            logger.error(f"Worker 循环异常: {e}", exc_info=True)
            await asyncio.sleep(1)  # 防止死循环


# ============================================================
# 事件路由
# ============================================================

async def _process_event(db, event: dict):
    """
    处理单条消息事件。

    event 字段:
        conversation_type ("group" | "dm"), group_id (群聊), session_id (私信),
        message_id, content, sender_type, sender_id, chain_depth
    """
    event_type = event.get("type", "")
    if event_type == "alarm":
        await _process_alarm_event(db, event)
        return
    if event_type == "trigger":
        await _process_trigger_event(db, event)
        return
    if event_type == "world_event":
        from app.ai.alarm import _process_world_event
        await _process_world_event(db, event)
        return

    conversation_type = event.get("conversation_type", "group")

    if conversation_type == "dm":
        await _process_dm_event(db, event)
    else:
        await _process_group_event(db, event)


async def _process_trigger_event(db, event: dict):
    """处理触发器事件：唤醒 AI 执行预设任务（与闹钟同机制）"""
    agent_id = event["agent_id"]
    trigger_id = event["trigger_id"]
    task = event["task"]

    from app.models.agent import Agent as AgentModel
    from app.ai.alarm import _process_alarm_event

    agent_result = await db.execute(select(AgentModel).where(AgentModel.id == agent_id))
    agent = agent_result.scalar_one_or_none()
    if agent is None:
        logger.warning(f"⏰ 触发器 #{trigger_id}: agent {agent_id} 不存在")
        return

    logger.info(f"⏰ 触发器 #{trigger_id}: AI {agent.name}({agent_id}) 执行任务 — 「{task[:60]}」")

    # 复用闹钟的唤醒执行链路（decide_action → LLM 工具循环）
    await _process_alarm_event(db, {
        "agent_id": agent_id,
        "alarm_id": trigger_id,
        "task": task,
    })


# ============================================================
# 私信事件处理
# ============================================================

async def _process_dm_event(db, event: dict):
    """处理私信事件：检查对方是否是 AI，如果是则触发回复"""
    session_id = event["session_id"]
    message_id = event["message_id"]
    content = event["content"]
    sender_id = event.get("sender_id")
    chain_depth = event.get("chain_depth", 0)
    force_own_key = event.get("force_own_key", False)  # v0.1.8

    from app.models.dm import DMSession
    from app.models.user import User
    from app.models.agent import Agent as AgentModel

    # 找到会话
    sess_result = await db.execute(
        select(DMSession).where(DMSession.session_id == session_id)
    )
    session = sess_result.scalar_one_or_none()
    if session is None:
        return

    # 找到接收方
    receiver_id = session.user2_id if session.user1_id == sender_id else session.user1_id

    # 检查是否是 AI
    user_result = await db.execute(
        select(User).where(User.id == receiver_id, User.type == "ai")
    )
    ai_user = user_result.scalar_one_or_none()
    if ai_user is None:
        return

    # 找到对应的 agent
    agent_result = await db.execute(
        select(AgentModel).where(AgentModel.user_id == receiver_id)
    )
    agent = agent_result.scalar_one_or_none()
    if agent is None:
        return

    logger.info(f"私信 {session_id} 触发 AI {agent.name}({agent.id}) 回复")

    # ── 2026-08-09: 聊天即情景——自动维护会话帧（无论是否真正回复，先记录「有人找过」）──
    try:
        from app.services.agent.state_stack_service import ensure_active_frame
        sender_row = await db.execute(
            select(User.username).where(User.id == sender_id)
        )
        sender_name = sender_row.scalar_one_or_none() or f"用户{sender_id}"
        await ensure_active_frame(
            db, agent.id, "dm", session_id,
            title=sender_name, actor_name=sender_name,
        )
    except Exception as e:
        logger.warning(f"DM 会话帧维护失败（非致命）: {e}")

    # DM 链条深度限制
    if chain_depth > 10:
        logger.info(f"DM {session_id} 对话链深度 {chain_depth} > 10，停止")
        return

    # 技能层：发布 message_received 事件（自治 Skill 感知）
    asyncio.create_task(_publish_skill_event({
        "type": "message_received",
        "data": {
            "conversation_type": "dm",
            "session_id": session_id,
            "content": content,
            "sender_type": "human",
            "sender_id": sender_id,
            "message_id": message_id,
        },
    }))

    # 简化的 DM 回复触发（不需要群聊那样的 DND/意愿检查）
    await _trigger_dm_ai_reply(
        db, agent, session_id, content, message_id,
        chain_depth=chain_depth + 1,
        sender_id=sender_id,
        force_own_key=force_own_key,
    )


async def _publish_skill_event(event: dict) -> None:
    """发布事件到 Skill 事件总线（技能层感知入口，失败不影响消息链路）"""
    try:
        from app.services.brain.skill_event_bus import skill_event_bus
        await skill_event_bus.publish(event)
    except Exception as e:
        logger.warning(f"技能事件发布失败（非致命）: {e}")


# ============================================================
# 群聊事件处理
# ============================================================

async def _process_group_event(db, event: dict):
    """处理群聊事件（原有逻辑）"""
    group_id = event["group_id"]
    message_id = event["message_id"]
    content = event["content"]
    sender_type = event.get("sender_type", "human")
    sender_id = event.get("sender_id")
    chain_depth = event.get("chain_depth", 0)

    # 远程消息门控：来自远程实例的 AI 消息不触发本地 AI 回复（防循环）
    source_public_id = event.get("source_public_id")
    if source_public_id and sender_type == "ai":
        logger.info(f"群 {group_id} 收到远程 AI 消息 (source={source_public_id})，跳过本地 AI 回复")
        return

    from app.models.agent import Agent as AgentModel

    # 获取群聊信息
    group_result = await db.execute(select(Group).where(Group.id == group_id))
    group = group_result.scalar_one_or_none()
    if group is None:
        return

    # 群管理暂停了 AI 触发
    if getattr(group, "is_paused", False):
        # 以前这里是静默 return：AI 不说话到底是没人 @、被暂停、还是没触发，查日志看不出来
        logger.info(f"⏸️ 群 {group_id}「{group.name}」已暂停 AI 触发（群设置里的暂停开关），本条消息不触发任何 AI")
        return
    # 对话链深度限制：根据群设置动态计算
    if group.owner_type == "ai":
        effective_max_depth = 50
    else:
        limit_per_min = group.speak_limit_per_minute or 0
        window_sec = group.speak_limit_window_seconds or 120
        if limit_per_min > 0:
            effective_max_depth = max(limit_per_min * 2, 5)
        else:
            effective_max_depth = 50

    if chain_depth > effective_max_depth:
        logger.info(
            f"群 {group_id} 对话链深度 {chain_depth} > {effective_max_depth}"
            f"(owner={group.owner_type}, limit={group.speak_limit_per_minute}/min)，停止触发"
        )
        return

    # 获取群聊中所有 AI 成员
    members_result = await db.execute(
        select(GroupMember).where(
            GroupMember.group_id == group_id,
            GroupMember.member_type == "ai",
        )
    )
    ai_members = members_result.scalars().all()

    if not ai_members:
        return

    # 确定需要触发的 AI 列表
    target_ai_ids: set[int] = set()

    # sender_id 统一为 user_id，与 group_members.member_id 直接对齐
    exclude_user_id = sender_id if sender_type == "ai" else None

    if sender_type == "human":
        target_ai_ids = {m.member_id for m in ai_members}
    else:
        target_ai_ids = {
            m.member_id for m in ai_members
            if m.member_id != (exclude_user_id if exclude_user_id else sender_id)
        }

    if not target_ai_ids:
        return

    # AI 自主决定——所有成员都触发，意愿分+提示词引导行为
    # 仅排除发送者自己（AI 消息时不触发自己）
    candidates = list(target_ai_ids)
    if sender_type == "ai" and exclude_user_id and exclude_user_id in candidates:
        candidates.remove(exclude_user_id)

    if not candidates:
        return

    # 全量消息下每个 AI 都要过一遍决策层：规则一次批量取，别按 AI 各查一次。
    # 候选是 user_id（group_members.member_id），规则按 agent.id 存，这里把映射一并取出来
    _rows = (await db.execute(
        select(AgentModel.id, AgentModel.user_id).where(AgentModel.user_id.in_(candidates))
    )).all()
    from app.services.world.decision_skill import load_rules_map
    _by_agent = await load_rules_map(db, "agent", [r[0] for r in _rows])
    rules_map = {r[1]: _by_agent.get(r[0], []) for r in _rows}

    logger.info(
        f"群聊 {group_id} 收到消息 (sender={sender_type}:{sender_id}, depth={chain_depth})，"
        f"触发 {len(candidates)} 个 AI: {candidates}"
    )

    # 群视界触发模式：群绑定世界时读取（默认 mention_only → 非 @ 消息不唤醒 LLM 本体；
    # 世界程序感知通道不受影响，见 docs/group_world/design/world_decision_skill.md §3）
    world_trigger_mode: str | None = None
    try:
        from app.models.world import WorldBinding, World as WorldModel
        wb_rows = (await db.execute(
            select(WorldBinding).where(
                WorldBinding.entity_type == "group",
                WorldBinding.entity_id == group_id,
            )
        )).scalars().all()
        if wb_rows:
            w = await db.get(WorldModel, wb_rows[0].world_id)
            if w is not None:
                world_trigger_mode = (w.config or {}).get("group_trigger_mode", "mention_only")
                logger.info(f"🔕 群 {group_id} 绑定世界 #{wb_rows[0].world_id}，触发模式={world_trigger_mode}")
    except Exception as e:
        logger.warning(f"群视界触发模式读取失败（非致命）: {e}")

    next_depth = chain_depth + 1
    from app.ai.chat_chain import chat_chain_manager

    # 通知群活跃（重置并发自动恢复倒计时）
    chat_chain_manager.notify_group_activity(group_id)

    # 判断消息优先级：@消息/公告 → 优先通道（上限1），普通消息 → 普通通道
    has_at = sender_type == "human" and "@" in (content or "")
    is_at_all = any(tag in (content or "") for tag in ("@all", "@everyone", "@全体"))
    is_priority_msg = has_at or is_at_all
    normal_sem = chat_chain_manager.get_semaphore(group_id, limit=getattr(group, "concurrent_ai_limit", 0))
    priority_sem = chat_chain_manager.get_priority_semaphore(group_id)

    for ai_id in candidates:
        if is_priority_msg:
            if not chat_chain_manager.try_claim_priority(ai_id, group_id):
                continue
            sem = priority_sem
        else:
            if not chat_chain_manager.try_claim(ai_id, group_id):
                continue
            sem = normal_sem

        async def _trigger_one(aid, chan_sem):
            async with chan_sem:
                try:
                    async with async_session() as inner_db:
                        await _maybe_trigger_ai_reply(
                            inner_db, aid, group_id, group, content, message_id,
                            chain_depth=next_depth,
                            sender_type=sender_type,
                            sender_id=sender_id,
                            message_type=event.get("message_type", "normal"),
                            world_trigger_mode=world_trigger_mode,
                            decision_rules=rules_map.get(aid),
                        )
                except Exception as e:
                    logger.error(f"AI {aid} 触发异常 (group={group_id}): {e}", exc_info=True)
                finally:
                    chat_chain_manager.release_claim(aid, group_id)

        asyncio.create_task(_trigger_one(ai_id, sem))

    # 技能层：发布 message_received 事件（自治 Skill 感知，fire-and-forget）
    asyncio.create_task(_publish_skill_event({
        "type": "message_received",
        "data": {
            "conversation_type": "group",
            "group_id": group_id,
            "content": content,
            "sender_type": sender_type,
            "sender_id": sender_id,
            "message_id": message_id,
        },
    }))

    # 群助手触发（独立实体，存 group_assistants 表）；它自己的消息不触发，防自触发。
    if not (sender_type == "ai" and (sender_id or 0) < 0):
        try:
            from app.models.world import GroupAssistant
            ga_rows = (await db.execute(
                select(GroupAssistant).where(GroupAssistant.group_id == group_id)
            )).scalars().all()
            for ga in ga_rows:
                asyncio.create_task(_trigger_group_assistant(
                    ga.id, group_id, content, message_id,
                    sender_type=sender_type, sender_id=sender_id,
                ))
        except Exception as e:
            logger.warning(f"群助手触发编排失败: {e}")


# ============================================================
# 编排：群聊回复触发
# ============================================================

# ============================================================
# 群助手触发（独立实体路径，不走 agent 体系）
# ============================================================

async def _trigger_group_assistant(
    assistant_id: int, group_id: int, content: str, trigger_message_id: int,
    sender_type: str = "human", sender_id: int | None = None,
):
    """群助手独立实体触发：@ 才唤醒（默认 mention_only），简化 LLM 链路——
    system_prompt + 最近群消息 → chat_completion → 发群消息 + 用量记账。
    与群视界 AI 同形态：无账号、无好友、不入群成员表。"""
    try:
        async with async_session() as db:
            from app.models.world import GroupAssistant, World
            from app.config import settings
            from app.utils.crypto import decrypt_api_key

            ga = await db.get(GroupAssistant, assistant_id)
            if ga is None:
                return
            world = await db.get(World, ga.world_id)

            # 触发模式：世界配置 group_trigger_mode（默认 mention_only → 非 @ 不唤醒）
            mode = (world.config or {}).get("group_trigger_mode", "mention_only") if world else "mention_only"
            # 群助手不是 users 行（无账号、不入群成员表），所以没有 id 可比：
            # 入口也不会把它的名字归一成 <@!id>，这里按名字认就是唯一正确的那条路
            is_mentioned = _check_mention(content, ga.name)
            is_at_all = any(tag in content for tag in ("@all", "@everyone", "@全体"))

            # 决策技能优先（AI 自写规则）：命中且 notify=false →
            # 程序化处理（reply 代发到群），不唤醒 LLM 本体
            try:
                from app.services.world.decision_skill import run_decision_engine, build_group_message_ctx
                sender_name = "群成员"
                if sender_id is not None:
                    from app.models.user import User as _U
                    _sr = (await db.execute(select(_U.username).where(_U.id == sender_id))).first()
                    sender_name = _sr[0] if _sr else f"用户{sender_id}"
                dec = await run_decision_engine(
                    db, "group_assistant", ga.id, world, "group_message",
                    build_group_message_ctx(
                        content, sender_id, sender_name, sender_type, group_id,
                        is_mention=is_mentioned, is_at_all=is_at_all,
                    ),
                )
                if dec.get("hit") and dec.get("handled"):
                    if dec.get("reply"):
                        from app.services.world.decision_skill import send_group_reply
                        await send_group_reply(db, group_id, -ga.id, dec["reply"], allow_non_member=True)
                    logger.info(f"🎲 群助手「{ga.name}」决策技能命中，程序化处理（不唤醒 LLM）")
                    return
            except Exception as e:
                logger.warning(f"🎲 群助手决策引擎异常（ga {ga.id}）: {e}")

            if mode == "mention_only" and not is_mentioned and not is_at_all:
                logger.info(f"🔕 群 {group_id} 群助手「{ga.name}」mention_only，非 @ 不触发")
                return

            # 构建消息：system_prompt + 最近群消息
            from app.chat.gm import get_gm_messages
            history = await get_gm_messages(db, group_id, limit=20)
            messages: list[dict] = [{"role": "system", "content": ga.system_prompt or f"你是群助手「{ga.name}」，服务本群。"}]
            for m in reversed(history):  # 正序
                if not m.content:
                    continue
                if m.sender_type == "human":
                    messages.append({"role": "user", "content": m.content})
                elif m.sender_type == "ai":
                    messages.append({"role": "assistant", "content": m.content})
            messages.append({"role": "user", "content": content})

            # API key 回落链：助手自定义 → 群主全局 → 系统默认（None 交给 chat_completion）
            api_key = None
            if ga.api_key_encrypted:
                try:
                    api_key = decrypt_api_key(ga.api_key_encrypted)
                except APIKeyDecryptError as e:
                    logger.warning(f"群助手 {ga.id} API Key 解密失败: {e}")
            api_base = ga.api_base_url
            if not api_key:
                try:
                    from sqlalchemy import text as _t
                    from app.models.user import User
                    owner_row = (await db.execute(_t(
                        "SELECT member_id FROM group_members WHERE group_id=:g AND member_type='human' AND role='owner' LIMIT 1"
                    ), {"g": group_id})).first()
                    owner_id = int(owner_row[0]) if owner_row else (world.owner_id if world else None)
                    if owner_id:
                        u = await db.get(User, owner_id)
                        if u and u.api_key_encrypted:
                            api_key = decrypt_api_key(u.api_key_encrypted)
                            api_base = u.api_base_url
                except Exception:
                    pass

            model = ga.model or settings.default_chat_model
            from app.ai.llm import chat_completion
            # 决策技能工具（群助手自配置：list/write/delete_decision_skill）
            from app.services.world.decision_skill import DECISION_TOOLS
            resp = await chat_completion(
                messages=messages, model=model,
                api_base_url=api_base or "", api_key=api_key,
                temperature=0.8, top_p=0.9, stream=False, db=db,
                tools=DECISION_TOOLS,
            )
            # 决策工具调用执行（自配置决策技能；仅工具调用时不回复，静默）
            for _tc in (resp or {}).get("tool_calls") or []:
                _fn = _tc.get("function") or {}
                _name = _fn.get("name") or ""
                if _name in ("list_decision_skills", "write_decision_skill", "delete_decision_skill"):
                    from app.tools.decision import handle_decision_tool
                    try:
                        await handle_decision_tool(db, "group_assistant", ga.id, _name, _fn.get("arguments") or "{}")
                    except Exception as e:
                        logger.warning(f"🎲 群助手决策工具执行失败: {e}")
            reply = ((resp or {}).get("content") or "").strip()
            if not reply:
                logger.info(f"群助手「{ga.name}」仅工具调用（决策技能配置），不回复")
                return

            # 发群消息（sender_id 用负值避免与 user_id 冲突；source=world 防触发世界程序循环）
            from app.chat.gm import send_gm_message
            from app.routers.ws import manager
            msg = await send_gm_message(db, group_id, "ai", -ga.id, reply, source="world", allow_non_member=True)
            await db.flush()
            try:
                await manager.broadcast_to_group(group_id, {"type": "message", "data": {"id": msg.id, "content": reply}})
            except Exception:
                pass

            # 用量记账：agent_id=-2 虚拟聚合「群助手」，记账人 = 世界主人
            try:
                from app.services.content.conversation_log_service import save_conversation_log
                await save_conversation_log(
                    db, agent_id=-2, messages=messages + [{"role": "assistant", "content": reply}],
                    conversation_type="group", group_id=group_id,
                    token_usage=(resp or {}).get("usage"), has_output=True, model=model,
                    user_id=world.owner_id if world else None,
                )
            except Exception:
                pass
            logger.info(f"🤖 群助手「{ga.name}」(ga {ga.id}) 回复群 {group_id}: {reply[:60]}")
    except Exception as e:
        logger.error(f"群助手 {assistant_id} 触发异常: {e}", exc_info=True)


async def _recheck_state_before_run(db, agent) -> bool:
    """执行前复查状态：入口快照可能已过期（skill 评估/建消息等耗时操作期间可能被封禁）。
    返回 False 表示应跳过本次回复。"""
    from app.models.agent import Agent as AgentModel
    fresh = (await db.execute(
        select(AgentModel.state).where(AgentModel.id == agent.id)
    )).scalar_one_or_none()
    if fresh == "blocked":
        logger.info(f"AI {agent.name}({agent.id}) 执行前复查状态为 blocked，跳过回复")
        return False
    return True


async def _maybe_trigger_ai_reply(
    db, agent_id: int, group_id: int, group, content: str, trigger_message_id: int,
    chain_depth: int = 0,
    sender_type: str = "human",
    sender_id: int | None = None,
    message_type: str = "normal",
    world_trigger_mode: str | None = None,
    decision_rules: list | None = None,
):
    """检查单个 AI 是否应该回复，如果是则调用 LLM 生成回复

    decision_rules：调用方批量预取的该 AI 决策规则（全量消息下省掉每个 AI 一次查库）
    """
    from app.services.agent.agent_service import get_agent
    from app.ai.decider import decide_action, ActionContext, ActionType
    from app.models.agent import Agent as AgentModel

    # v2.0.0: 群触发传入的 agent_id = group_members.member_id = user_id（7-3 已统一）
    # ⚠️ 必须先用 user_id 查：否则 user_id 恰好等于某个 agent.id 时会错配（如涵吾珑 user_id=4 撞上任熠航 agent.id=4）
    agent_result = await db.execute(
        select(AgentModel).where(AgentModel.user_id == agent_id)
    )
    agent = agent_result.scalar_one_or_none()
    if agent is None:
        # 老数据 fallback：按 agent.id 查（DM 路径传 agent.id 时兼容）
        agent = (await get_agent(db, agent_id)).ok
    if agent is None:
        logger.warning(f"AI agent_id/user_id={agent_id} 不存在，跳过")
        return

    # 统一使用 agent.id（数据库主键）作为后续内部标识
    resolved_agent_id = agent.id

    # ── 2026-08-09: 聊天即情景——群聊触发时维护会话帧（即使 DND/意愿不回复也记录「有人找过」）──
    try:
        from app.services.agent.state_stack_service import ensure_active_frame
        from app.models.user import User as UserModel
        sender_name = "群成员"
        if sender_id is not None:
            srow = await db.execute(select(UserModel.username).where(UserModel.id == sender_id))
            sender_name = srow.scalar_one_or_none() or f"用户{sender_id}"
        await ensure_active_frame(
            db, resolved_agent_id, "group_chat", f"group:{group_id}",
            title=(group.name if group and getattr(group, "name", None) else f"群{group_id}"),
            actor_name=sender_name,
        )
    except Exception as e:
        logger.warning(f"群会话帧维护失败（非致命）: {e}")

    logger.info(f"🔍 检查 AI {agent.name}(id={resolved_agent_id}, user_id={agent.user_id}), state={agent.state}")

    # 两种写法都认：正文里通常是入口归一后的 <@!id>，历史消息里还是 @名字
    is_mentioned = _check_mention(content, agent.name, agent.user_id)
    logger.info(f"🔍 AI {agent.name}(id={resolved_agent_id}): is_mentioned={is_mentioned}, content_preview='{content[:80]}'")

    # v0.1.4: 使用统一决策（替代原有 Gate 1-5 的手动判断）
    # v0.2.1: 检测 DND 穿透条件
    is_at_all = any(tag in content for tag in ("@all", "@everyone", "@全体"))
    is_announcement = message_type == "announcement"

    # 决策技能优先于触发模式（AI 自写规则 > 平台默认兜底）。
    # 不要求绑定世界；命中的代发标 source="world" 防回灌。见 docs/dev/decision_layer.md。
    decision_note = ""
    try:
        from app.services.world.decision_skill import run_decision_engine, build_group_message_ctx
        dec = await run_decision_engine(
            db, "agent", resolved_agent_id, None, "group_message",
            build_group_message_ctx(
                content, sender_id, sender_name, sender_type, group_id,
                is_mention=is_mentioned, is_at_all=is_at_all,
            ),
            rules=decision_rules,
        )
        if dec.get("hit") and dec.get("handled"):
            if dec.get("reply"):
                try:
                    from app.services.world.decision_skill import send_group_reply
                    await send_group_reply(db, group_id, agent.user_id or 0, dec["reply"])
                except Exception:
                    pass
            logger.info(f"🎲 AI {agent.name}(id={resolved_agent_id}) 决策技能命中，程序化处理（不唤醒 LLM）")
            return
        decision_note = dec.get("note") or ""
    except Exception as e:
        logger.warning(f"🎲 AI {agent.name} 决策引擎异常: {e}")

    # 群视界触发模式拦截：mention_only 时非 @ 消息不唤醒 LLM 本体
    # （会话帧维护已在上方完成，「有人找过」照常记录；世界程序感知通道不受影响）
    if world_trigger_mode == "mention_only" and not is_mentioned and not is_at_all and not is_announcement:
        logger.info(f"🔕 群 {group_id} 群视界 mention_only，非 @ 消息不触发 {agent.name}(id={resolved_agent_id})")
        return
    # v2.0.6: 检查发送者是否为特别关心好友
    is_priority_friend = False
    if sender_type == "human" and sender_id:
        try:
            from app.models.friendship import Friendship
            f_result = await db.execute(
                select(Friendship).where(
                    Friendship.user_id == agent.owner_id,
                    Friendship.friend_type == "human",
                    Friendship.friend_id == sender_id,
                    Friendship.is_priority == True,
                )
            )
            is_priority_friend = f_result.scalar_one_or_none() is not None
        except Exception:
            pass
    ctx = ActionContext(
        event_type="message",
        agent_id=resolved_agent_id,
        group_id=group_id,
        content=content,
        sender_type=sender_type,
        sender_id=sender_id,
        is_mentioned=is_mentioned,
        is_at_all=is_at_all,
        is_announcement=is_announcement,
        is_priority_friend=is_priority_friend,
        chain_depth=chain_depth,
    )
    decision = await decide_action(db, agent, ctx)
    logger.info(f"🔍 AI {agent.name}(id={resolved_agent_id}): decision={decision.action_type.value}, "
                f"priority={decision.priority}, reason={decision.reason}")

    if not decision.should_act:
        # 处理 DND 暂存消息
        if decision.details.get("store_pending"):
            await chat_api.store_pending(db, resolved_agent_id, group_id, trigger_message_id)
        return

    # 记录意愿（如果决策中有）
    w_score = decision.willingness_score
    if w_score > 0:
        agent.last_willingness_score = w_score
        agent.last_willingness_reason = decision.reason

    # 4. 速率限制检查
    if not _check_rate_limit(resolved_agent_id):
        logger.info(f"AI {agent.name}(id={resolved_agent_id}) 速率限制，跳过")
        return

    # 4.5. 忙时中断注入
    if await is_agent_running(agent.id):
        await add_pending_interrupt(agent.id, {
            "type": "user_message",
            "content": content,
            "group_id": group_id,
            "sender_id": sender_id,
        })
        logger.info(f"AI {agent.name}({agent.id}) 正忙，群聊中断消息已注入")
        return

    # 5. 获取 API 配置（v0.1.4: 公共辅助函数；v0.1.5: 四层优先链含池 Key）
    # conversation_type 必须传：文档里的规则是"群聊扣群主（group_owner_pays）"，
    # 不传就会被当成私聊、落到聊天者头上 —— QQ 群@来的影子账号没有额度，解析必然为空。
    api_key, api_base, credit_source, pool_key_id, provider_info = await _get_api_config(
        db, agent, chatter_id=sender_id, conversation_type="group"
    )
    logger.info(f"🔍 AI {agent.name}: api_base={api_base}, has_api_key={api_key is not None}, "
                f"credit_source={credit_source}")

    # 5.1. 无 API Key → 发送系统通知后跳过
    if api_key is None:
        logger.warning(f"AI {agent.name}({agent.id}) 无 API Key，发送系统通知")
        await _send_system_error(db, agent, "no_api_key", "", "group", group_id, None)
        return

    # 5.5. 中断标记：如果 AI 之前在忙，记录中断
    try:
        from app.services.agent.workspace_service import mark_interrupted
        sender_info = f"群聊 #{group_id} 的新消息"
        await mark_interrupted(db, resolved_agent_id, reason=sender_info)
    except Exception:
        pass  # 非致命

    # 5.6. Skill 引擎评估（延迟回复、打字指示器）
    from app.services.skill.skill_engine import evaluate_action_skills, _is_delay_reply_allowed
    skill_result = await evaluate_action_skills(db, agent, group_id, context={
        "content": content,
        "sender_type": sender_type,
        "sender_id": sender_id,
    })
    # 延迟回复（若已有积压消息则跳过，避免级联延迟）
    delay_skipped = False
    if skill_result.delay_seconds > 0:
        pending = await chat_api.get_pending(db, resolved_agent_id, group_id)
        pending_count = len(pending) if pending else 0
        if pending_count > 0:
            logger.info(f"🧠 AI {agent.name} 有 {pending_count} 条积压消息，跳过延迟回复")
            delay_skipped = True
        else:
            logger.info(f"🧠 AI {agent.name} 技能延迟 {skill_result.delay_seconds}s")
            await asyncio.sleep(skill_result.delay_seconds)

    # v0.1.3: trigger_user_id 用于通用/半通用 AI 的 per-user 记忆隔离
    trigger_user_id = sender_id if sender_type == "human" else None

    # 6. 获取有效配置（v0.1.3: per-user 覆盖 — 需在 build_messages 前获取）
    from app.services.agent.agent_service import get_effective_config
    effective_cfg = await get_effective_config(db, agent.id, trigger_user_id)
    logger.info(f"🔍 AI {agent.name}: effective_cfg ai_type={effective_cfg['ai_type']}, "
                f"thinking={effective_cfg['thinking_enabled']}, temp={effective_cfg['temperature']}")

    # 7. 构建消息
    from app.ai.llm import build_messages, resolve_model
    # 向量加速混合检索仅在 AI 全群启用（AI 内部协作场景）
    from app.ai.group_logic import is_ai_only_group
    ai_only = await is_ai_only_group(db, group_id, group=group)
    use_vector = group.is_vector_accelerated and ai_only
    if group.is_vector_accelerated and not ai_only:
        logger.info(f"群 {group_id} 含人类成员，跳过向量加速（使用常规历史窗口）")
    messages = await build_messages(
        db, agent, group_id,
        vector_accelerated=use_vector,
        api_base_url=api_base,
        api_key=api_key,
        trigger_user_id=trigger_user_id,
        system_prompt_override=effective_cfg.get("system_prompt"),
    )
    logger.info(f"🔍 AI {agent.name}: 构建了 {len(messages)} 条消息")

    # notify=true 的决策技能：动作已由程序跑完，结果必须让本体看见，否则它会重复做一遍
    if decision_note:
        messages.append({"role": "system", "content": decision_note})

    # DND 被 @ 穿透时，提醒 AI 重新评估免打扰/聊天链状态
    if decision.details.get("dnd_penetrated"):
        messages.append({"role": "system", "content": (
            "⚠️ 你之前设了群免打扰，但被 @（或 @all/群公告）穿透了。"
            "请评估：① 是否需要重新设置免打扰？② 是否要退出当前聊天链（间隔 < 2 分钟 = 同链）？"
            "如果只是来答一个问题，答完后用 set_dnd 设短时免打扰安静回去。"
        )})

    # 延迟被跳过时，注入提醒：加快回复速度 + 记入记忆
    if delay_skipped:
        delay_hint = (
            "⚠️ 系统提醒：你配置了延迟回复，但因为群里有积压消息，延迟已被跳过。\n"
            "请检查最近的发消息者——对方可能正在等你回复。\n"
            "建议：\n"
            "1. 加快对此人的回复速度，不要再设长延迟\n"
            "2. 调用 manage_workspace 在 todo 里记下「被催促回复，需要调整回复节奏」\n"
            "3. 调用 store_memory 记下这个交互模式，以后遇到此人时优先快速响应"
        )
        messages.append({"role": "system", "content": delay_hint})

    # 7.5 获取工具（能力版本化：按 effective 版本取定义快照，前缀缓存稳定）
    from app.repositories.capability_repo import SQLAlchemyCapabilityRepository
    from app.services.tool_registry import get_allowed_tools
    from app.services.capability_versioning import get_effective_definitions, SOURCE_PLATFORM
    delay_allowed = await _is_delay_reply_allowed(db, agent)
    current_tools = get_allowed_tools(agent.state, thinking_enabled=effective_cfg["thinking_enabled"], delay_reply_allowed=delay_allowed)
    allowed_names = {t["function"]["name"] for t in current_tools}
    effective_defs = await get_effective_definitions(SQLAlchemyCapabilityRepository(db), agent, SOURCE_PLATFORM, current_tools)
    tools = [d for d in effective_defs if ((d or {}).get("function") or {}).get("name") in allowed_names]

    # + 绑定世界的世界侧 skills（居民能力；群绑定或 agent 直接绑定；effective 版本快照，版本化懒加载）
    # 同名冲突策略（2026-08-07）：同名 skill 只注入一个定义（当前群绑定世界优先），
    # 工具定义自带可选 world_id 参数，AI 可指定其他世界的同名版本（dispatch 校验绑定后路由）
    try:
        from app.services.world.world_service import find_worlds_by_entity
        from app.services.world.world_skill_runtime import build_world_tools
        from app.services.capability_versioning import ensure_world_version, get_effective_definitions as _get_eff
        agent_worlds = await find_worlds_by_entity(db, "agent", agent.user_id or 0)
        group_worlds = await find_worlds_by_entity(db, "group", group_id) if group_id is not None else []
        # 优先级：当前群绑定世界 > agent 直接绑定（同名时保留群绑定版本）
        ordered = [*group_worlds, *agent_worlds]
        seen_w = set()
        world_by_name: dict[str, tuple] = {}   # skill 名 → (世界, 定义)
        multi_worlds: dict[str, list[int]] = {}  # skill 名 → 颁布它的所有世界 id（清单注明）
        for w in ordered:
            if w.id in seen_w:
                continue
            seen_w.add(w.id)
            wtools = build_world_tools(w.id)
            if wtools:
                from app.repositories.capability_repo import SQLAlchemyCapabilityRepository
                await ensure_world_version(SQLAlchemyCapabilityRepository(db), w.id, wtools)
            eff = await _get_eff(db, agent, f"world-{w.id}", wtools)
            for d in eff:
                nm = ((d or {}).get("function") or {}).get("name")
                if not nm:
                    continue
                multi_worlds.setdefault(nm, []).append(w.id)
                if nm not in world_by_name:  # 群绑定世界先遍历 → 同名保留群绑定版本
                    world_by_name[nm] = (w, d)
        tools = [*tools, *(d for _, d in world_by_name.values())]
        # 同名多世界信息（供清单告知 AI 可用 world_id 指定）
        dup_names = {nm: wids for nm, wids in multi_worlds.items() if len(wids) > 1}
    except Exception as e:
        logger.warning(f"🌐 AI {agent.name} 世界能力注入失败: {e}")
        dup_names = {}

    model = resolve_model(agent, global_default_model=provider_info.get("global_default_chat_model"))
    logger.info(f"🔍 AI {agent.name}: model={model}, tools={len(tools)}")

    # 8. 工具调用循环（含思考状态广播）
    conv_key = f"group:{group_id}"
    _thinking_state.setdefault(conv_key, {})[agent.id] = {
        "name": agent.name, "avatar_url": agent.avatar_url,
    }
    logger.info(f"🚀 AI {agent.name}: 开始调用 LLM...")
    try:
        await chat_api.broadcast_to_group(
            group_id,
            {
                "type": "ai_thinking",
                "data": {
                    "user_id": agent.user_id,
                    "agent_name": agent.name,
                    "agent_avatar_url": agent.avatar_url,
                    "group_id": group_id,
                    "trigger": "user",
                },
            },
        )
        if not await _recheck_state_before_run(db, agent):
            return
        await _run_serialized(agent, _tool_call_loop(
            db=db,
            agent=agent,
            group_id=group_id,
            messages=messages,
            tools=tools,
            model=model,
            api_base_url=api_base,
            api_key=api_key,
            max_loops=effective_cfg["max_tool_rounds"],
            chain_depth=chain_depth,
            conversation_type="group",
            trigger_user_id=trigger_user_id,
            effective_cfg=effective_cfg,
            credit_source=credit_source,
            pool_key_id=pool_key_id,
            provider_supports_thinking=provider_info.get("thinking_supported"),
            trigger="user",
            is_federated=False,
        ))
    except Exception as e:
        logger.error(f"❌ AI {agent.name} 群聊回复异常 (group={group_id}): {e}", exc_info=True)
    finally:
        _thinking_state.get(conv_key, {}).pop(agent.id, None)
        await chat_api.broadcast_to_group(
            group_id,
            {
                "type": "ai_thinking_end",
                "data": {
                    "user_id": agent.user_id,
                    "agent_name": agent.name,
                    "agent_avatar_url": agent.avatar_url,
                    "group_id": group_id,
                    "trigger": "user",
                },
            },
        )
    logger.info(f"✅ AI {agent.name}: LLM 调用完成")
    # 回复后标记退出当前聊天链（尺时间计时开始）
    try:
        from app.ai.chat_chain import chat_chain_manager
        chat_chain_manager.mark_replied(resolved_agent_id, group_id)
    except Exception:
        pass

    # 10. 标记未读消息已处理
    await chat_api.mark_pending_read(db, resolved_agent_id, group_id)
    await db.commit()


# ============================================================
# 编排：DM 回复触发
# ============================================================

async def _trigger_dm_ai_reply(
    db,
    agent,
    session_id: str,
    content: str,
    trigger_message_id: int,
    chain_depth: int = 0,
    sender_id: int | None = None,
    force_own_key: bool = False,
):
    """触发 AI 对私信的自动回复"""
    from app.services.agent.agent_service import get_agent
    from app.models.user import User as UserModel

    # 提前捕获 agent 属性（防止 session 过期后 DetachedInstanceError）
    agent_id = agent.id
    agent_name = agent.name
    agent_state = agent.state
    agent_avatar = agent.avatar_url
    agent_ai_type = agent.ai_type

    # 状态检查
    if agent_state == "blocked":
        logger.info(f"AI {agent_name}({agent_id}) 状态为 blocked，跳过 DM 回复")
        return

    # 速率限制
    if not _check_rate_limit(agent_id):
        logger.info(f"AI {agent_name}({agent_id}) 速率限制，跳过 DM 回复")
        return

    # ── 忙时中断注入 ──
    if await is_agent_running(agent_id):
        await add_pending_interrupt(agent_id, {
            "type": "user_message",
            "content": content,
            "session_id": session_id,
            "sender_id": sender_id,
        })
        logger.info(f"AI {agent_name}({agent_id}) 正忙，DM 中断消息已注入")
        return

    # 获取有效配置（v0.1.3: per-user 覆盖 — DM 场景 trigger_user_id=sender_id）
    from app.services.agent.agent_service import get_effective_config as _get_eff_cfg
    effective_cfg = await _get_eff_cfg(db, agent_id, sender_id)

    # 获取 API 配置（v0.1.8: 按 AI 类型 + force_own_key 决定账单人）
    # 外部通道（QQ 等）来的私聊：对方不是平台用户，账单记在 AI 主人头上，
    # 否则"通用/半通用 AI 私聊扣聊天者"这条规则会解析成空 Key，AI 只能发系统通知。
    from app.models.user import User as _UserModel

    _chatter = await db.get(_UserModel, sender_id) if sender_id else None
    api_key, api_base, credit_source, pool_key_id, provider_info = await _get_api_config(
        db, agent,
        chatter_id=sender_id,
        force_own_key=force_own_key,
        conversation_type="dm",
        bill_to_owner=bool(getattr(_chatter, "origin_channel", None)),
    )

    # 无 API Key → 发送 DM 系统通知后跳过
    if api_key is None:
        logger.warning(f"AI {agent_name}({agent_id}) 无 API Key，发送 DM 系统通知")
        await _send_system_error(db, agent, "no_api_key", "", "dm", None, session_id)
        return

    # 中断标记：如果 AI 之前在忙，记录中断
    try:
        from app.services.agent.workspace_service import mark_interrupted
        await mark_interrupted(db, agent_id, reason=f"私信 {session_id} 的新消息")
    except Exception:
        pass  # 非致命

    # Skill 引擎评估（延迟回复、打字指示器）
    from app.services.skill.skill_engine import evaluate_action_skills, _is_delay_reply_allowed
    skill_result = await evaluate_action_skills(db, agent, 0, context={
        "content": content,
        "sender_type": "human",  # DM 中对方是人类
    })
    if skill_result.delay_seconds > 0:
        await asyncio.sleep(skill_result.delay_seconds)

    # 构建消息
    from app.ai.llm import build_dm_messages, resolve_model
    # v0.1.3: DM 中 sender_id 即为触发用户
    messages = await build_dm_messages(db, agent, session_id, api_base_url=api_base, api_key=api_key, trigger_user_id=sender_id, system_prompt_override=effective_cfg.get("system_prompt"))

    # 获取工具（能力版本化：按 effective 版本取定义快照）
    from app.repositories.capability_repo import SQLAlchemyCapabilityRepository
    from app.services.tool_registry import get_allowed_tools
    from app.services.capability_versioning import get_effective_definitions, SOURCE_PLATFORM
    delay_allowed = await _is_delay_reply_allowed(db, agent)
    current_tools = get_allowed_tools(agent_state, thinking_enabled=effective_cfg["thinking_enabled"], delay_reply_allowed=delay_allowed)
    allowed_names = {t["function"]["name"] for t in current_tools}
    effective_defs = await get_effective_definitions(SQLAlchemyCapabilityRepository(db), agent, SOURCE_PLATFORM, current_tools)
    tools = [d for d in effective_defs if ((d or {}).get("function") or {}).get("name") in allowed_names]
    model = resolve_model(agent, global_default_model=provider_info.get("global_default_chat_model"))

    logger.info(f"🚀 AI {agent_name}: 开始 DM 回复 (session={session_id})")

    conv_key = f"dm:{session_id}"
    _thinking_state.setdefault(conv_key, {})[agent_id] = {
        "name": agent_name, "avatar_url": agent_avatar,
    }
    try:
        await chat_api.broadcast_to_dm(
            session_id,
            {
                "type": "ai_thinking",
                "conversation_type": "dm",
                "data": {
                    "agent_id": agent_id,
                    "agent_name": agent_name,
                    "agent_avatar_url": agent_avatar,
                    "session_id": session_id,
                    "trigger": "user",
                },
            },
        )
        if not await _recheck_state_before_run(db, agent):
            return
        await _run_serialized(agent, _tool_call_loop(
            db=db,
            agent=agent,
            group_id=None,  # DM 不使用 group_id
            messages=messages,
            tools=tools,
            model=model,
            api_base_url=api_base,
            api_key=api_key,
            max_loops=effective_cfg["max_tool_rounds"],
            chain_depth=chain_depth,
            conversation_type="dm",
            session_id=session_id,
            trigger_user_id=sender_id,
            effective_cfg=effective_cfg,
            credit_source=credit_source,
            pool_key_id=pool_key_id,
            provider_supports_thinking=provider_info.get("thinking_supported"),
            trigger="user",
        ))
    except Exception as e:
        logger.error(f"❌ AI {agent_name} DM 回复异常 (session={session_id}): {e}", exc_info=True)
    finally:
        _thinking_state.get(conv_key, {}).pop(agent_id, None)
        await chat_api.broadcast_to_dm(
            session_id,
            {
                "type": "ai_thinking_end",
                "conversation_type": "dm",
                "data": {
                    "agent_id": agent_id,
                    "agent_name": agent_name,
                    "agent_avatar_url": agent_avatar,
                    "session_id": session_id,
                    "trigger": "user",
                },
            },
        )
    logger.info(f"✅ AI {agent_name}: DM 回复完成")

    # 提交工作区变更（中断标记、任务保存等）
    await db.commit()
