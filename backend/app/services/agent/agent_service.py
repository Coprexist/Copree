"""
AI 代理服务
处理 AI 代理的 CRUD、配置修改、状态切换、回滚等
"""
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from app.repositories.agent_repo import AgentRepository, SQLAlchemyAgentRepository
from sqlalchemy import select, func, update
from app.models.user import User
from app.models.agent import Agent, AgentConfigHistory, AgentUserConfig, AgentCollaborator
from app.models.memory import RoughMemory, DetailMemory
from app.config import settings
from app.utils.result import Result
from app.utils.text import extract_mentions, mentions_user
from app.utils.pure.presets import merge_preset_values
from app.utils.pure.willingness import (
    WillingnessResult, calc_alarm_willingness, calc_reply_willingness, calc_proactive_willingness,
)
from app.utils.pure.timeutil import utc_now

logger = logging.getLogger(__name__)


def _ensure_repo(db_or_repo):
    """兼容旧调用：传入 AsyncSession 时包装为 SQLAlchemyAgentRepository。"""
    if isinstance(db_or_repo, AsyncSession):
        return SQLAlchemyAgentRepository(db_or_repo)
    return db_or_repo


# 三档 AI 配置预设值
CONFIG_PROFILES = {
    "chat": {
        "name": "聊天档",
        "description": "被动响应 · 低成本 — 只回答你问的，不多说一句",
        "ai_type": "general",
        # 模型参数
        "temperature": 0.7, "top_p": 0.9, "presence_penalty": 0.3, "frequency_penalty": 0.3,
        "thinking_enabled": False,
        # 工具调用
        "max_tool_rounds": 6,
        "alarm_max_tool_rounds": 8,
        # 闹钟 / 心跳
        "force_alarm_on_end": False,
        "max_alarms": 3,
        # 行为开关
        "delay_reply_enabled": False,
        "is_ai_editable": False,
        "hide_ai_identity": True,
        "reminder_grace": "every_time",
        # v0.1.6: 文件记忆
        "memory_load_mode": "index_only",
        "memory_recent_count": 0,
    },
    "immersive": {
        "name": "深度沉浸档",
        "description": "半自主 · 按需参与 — 能自己进群、深度响应，但不主动制造话题",
        "ai_type": "resonance",
        # 模型参数
        "temperature": 0.9, "top_p": 0.95, "presence_penalty": 0.5, "frequency_penalty": 0.5,
        "thinking_enabled": True,
        # 工具调用
        "max_tool_rounds": 8,
        "alarm_max_tool_rounds": 10,
        # 闹钟 / 心跳
        "force_alarm_on_end": False,
        "max_alarms": 5,
        # 行为开关
        "delay_reply_enabled": True,
        "is_ai_editable": True,
        "hide_ai_identity": False,
        "reminder_grace": "every_time",
        # v0.1.6: 文件记忆
        "memory_load_mode": "index_plus_recent",
        "memory_recent_count": 3,
    },
    "digital_life": {
        "name": "数字生命档",
        "description": "持续在线 · 主动行为 — 自己思考、整理、交友、冲浪",
        "ai_type": "resonance",
        # 模型参数
        "temperature": 1.1, "top_p": 0.95, "presence_penalty": 0.6, "frequency_penalty": 0.6,
        "thinking_enabled": True,
        # 工具调用
        "max_tool_rounds": 10,
        "alarm_max_tool_rounds": 15,
        # 闹钟 / 心跳
        "force_alarm_on_end": True,
        "max_alarms": 20,
        # 行为开关
        "delay_reply_enabled": True,
        "is_ai_editable": True,
        "hide_ai_identity": False,
        "reminder_not_count": True,
        # v0.1.6: 文件记忆
        "memory_load_mode": "index_plus_semantic",
        "memory_recent_count": 5,
    },
}


# 预设档位顺序（用于判断升降级方向）——已迁移到 utils/pure/presets.py
# 强相关参数列表 ——已迁移到 utils/pure/presets.py
# _merge_preset_values ——已迁移到 utils/pure/presets.py，导入为 merge_preset_values

async def apply_config_profile(
    db: AsyncSession,
    agent_id: int,
    profile: str,
    operator_id: int | None = None,
    dry_run: bool = False,
) -> Agent | dict:
    """
    应用（或预览）预设配置档。

    按升降级规则智能合并，保护用户手动调整。

    设置 dry_run=True 返回预览 dict 而非实际写入。
    """
    db = _ensure_repo(db)
    if profile not in CONFIG_PROFILES:
        raise ValueError(f"无效的配置档: {profile}，可选: {list(CONFIG_PROFILES.keys())}")

    agent = (await get_agent(db, agent_id)).unwrap()

    preset = CONFIG_PROFILES[profile]
    old_profile = agent.config_profile or "chat"

    # 收集当前强相关值
    current_values = {
        "temperature": agent.current_temperature,
        "top_p": agent.current_top_p,
        "presence_penalty": agent.current_presence_penalty,
        "frequency_penalty": agent.current_frequency_penalty,
        "thinking_enabled": agent.thinking_enabled,
        "max_tool_rounds": agent.max_tool_rounds,
        "alarm_max_tool_rounds": agent.alarm_max_tool_rounds,
        "force_alarm_on_end": agent.force_alarm_on_end,
        "max_alarms": agent.max_alarms,
        "is_ai_editable": agent.is_ai_editable,
        "memory_recent_count": agent.memory_recent_count,
        "memory_load_mode": agent.memory_load_mode,
        "memory_shared_scope": agent.memory_shared_scope,
        "reminder_grace": agent.reminder_grace,
    }

    # 合并
    merged, changed = merge_preset_values(old_profile, profile, current_values, preset)

    # 预览模式
    if dry_run:
        changes_detail = {}
        for key in changed:
            changes_detail[key] = {
                "old": current_values.get(key),
                "new": merged[key],
            }
        return {
            "direction": "upgrade" if _PRESET_ORDER.get(profile, 0) > _PRESET_ORDER.get(old_profile, 0) else "downgrade",
            "old_profile": old_profile,
            "new_profile": profile,
            "changed_fields": changes_detail,
            "unchanged_strong": [k for k in _STRONG_NUMERIC_PARAMS + _STRONG_BOOL_PARAMS if k in preset and k not in changed],
            "independent_untouched": [k for k in _INDEPENDENT_PARAMS if hasattr(agent, k)],
        }

    # 实际写入
    # 保存历史快照
    history = AgentConfigHistory(
        agent_id=agent.id,
        system_prompt=agent.current_system_prompt,
        temperature=agent.current_temperature,
        top_p=agent.current_top_p,
        presence_penalty=agent.current_presence_penalty,
        frequency_penalty=agent.current_frequency_penalty,
    )
    db.add(history)

    # 写入合并后的强相关值
    for key, value in merged.items():
        if key in ("temperature", "top_p", "presence_penalty", "frequency_penalty"):
            setattr(agent, f"current_{key}", value)
        elif hasattr(agent, key):
            setattr(agent, key, value)

    # 档位标记
    agent.config_profile = profile

    await db.flush()
    logger.info(
        f"🎚️ AI({agent_id}) 切换预设: {old_profile} → {profile}"
        f"（{'升级' if _PRESET_ORDER.get(profile,0) > _PRESET_ORDER.get(old_profile,0) else '降级'}），"
        f"变更 {len(changed)} 项: {changed}"
    )
    return agent


async def create_agent(
    db: AsyncSession,
    owner_id: int,
    name: str,
    system_prompt: str | None = None,
    temperature: float = 0.8,
    top_p: float = 0.9,
    presence_penalty: float = 0.5,
    frequency_penalty: float = 0.5,
    chat_model: str | None = None,
    work_model: str | None = None,
    thinking_enabled: bool = False,
    is_admin: bool = False,
    api_credit_cost: int = 0,
    hide_ai_identity: bool = False,
    delay_reply_enabled: bool | None = None,
    config_profile: str | None = None,
    max_tool_rounds: int = 3,
    alarm_max_tool_rounds: int = 10,
    force_alarm_on_end: bool = False,
    max_alarms: int = 10,
    is_ai_editable: bool = True,
    ai_type: str = "resonance",
    reminder_grace: str = "every_time",
    allow_friend_requests: bool = True,
    auto_respond_friend_request: bool = False,
    discoverable: bool = True,
    memory_load_mode: str = "index_only",
    memory_recent_count: int = 0,
    memory_shared_scope: str = "private_only",
    bio: str | None = None,
    status_text: str | None = None,
    allow_others_chat: bool = True,
    others_chat_mode: str = "unlimited",
    others_chat_quota: int = 30,
    others_chat_used: int = 0,
    disallow_mode: str = "strict",
    auto_dnd_threshold: int = 20,
    auto_dnd_duration: int = 5,
    conversation_logs_limit: int | None = None,
    user_can_view_logs: bool | None = None,
) -> Agent:
    """
    创建 AI 代理。
    管理员创建不限额度；普通用户需有剩余 ai_quota（仅作上限检查，不扣除）。
    实际 API 调用消耗 api_credit。

    通用 AI（general）自动设置 hide_ai_identity=True。
    """
    db = _ensure_repo(db)
    # 查询用户（额度检查和日志都需要）
    result = await db.execute(select(User).where(User.id == owner_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise ValueError("用户不存在")

    if not is_admin:
        if user.ai_quota <= 0:
            raise ValueError("AI 创建额度不足，请联系管理员获取兑换码")

    # 创建 Agent 对应的 users 条目（统一 ID 空间，用于私信等场景）
    # username 唯一（AI 无登录语义）：撞名加短后缀，显示名（agent.name）不受影响
    from uuid import uuid4 as _uuid4
    username = name
    dup = (await db.execute(select(User.id).where(User.username == name))).first()
    if dup:
        username = f"{name}-{_uuid4().hex[:4]}"
    ai_user = User(
        username=username,
        type="ai",
        password_hash="",
        role="ai",
        is_active=True,
    )
    db.add(ai_user)
    await db.flush()

    # 通用 AI 默认隐藏 AI 身份
    if ai_type == "general":
        hide_ai_identity = True

    # 解析 delay_reply_enabled：None = 继承全局默认
    if delay_reply_enabled is None:
        from app.models.conversation_log import ConversationLogConfig
        cfg_result = await db.execute(select(ConversationLogConfig).limit(1))
        cfg = cfg_result.scalar_one_or_none()
        delay_reply_enabled = cfg.default_delay_reply_enabled if cfg else False

    # 创建 Agent
    agent = Agent(
        owner_id=owner_id,
        name=name,
        user_id=ai_user.id,
        original_system_prompt=system_prompt,
        original_temperature=temperature,
        original_top_p=top_p,
        original_presence_penalty=presence_penalty,
        original_frequency_penalty=frequency_penalty,
        current_system_prompt=system_prompt,
        current_temperature=temperature,
        current_top_p=top_p,
        current_presence_penalty=presence_penalty,
        current_frequency_penalty=frequency_penalty,
        chat_model=chat_model,
        work_model=work_model,
        thinking_enabled=thinking_enabled,
        config_profile=config_profile,
        state="active",
        api_credit_cost=api_credit_cost,
        hide_ai_identity=hide_ai_identity,
        delay_reply_enabled=delay_reply_enabled,
        max_tool_rounds=max_tool_rounds,
        alarm_max_tool_rounds=alarm_max_tool_rounds,
        force_alarm_on_end=force_alarm_on_end,
        max_alarms=max_alarms,
        is_ai_editable=is_ai_editable,
        ai_type=ai_type,
        reminder_grace=reminder_grace,
        allow_friend_requests=allow_friend_requests,
        auto_respond_friend_request=auto_respond_friend_request,
        discoverable=discoverable,
        memory_load_mode=memory_load_mode,
        memory_recent_count=memory_recent_count,
        memory_shared_scope=memory_shared_scope,
        bio=bio,
        status_text=status_text,
        allow_others_chat=allow_others_chat,
        others_chat_mode=others_chat_mode,
        others_chat_quota=others_chat_quota,
        others_chat_used=others_chat_used,
        disallow_mode=disallow_mode,
        auto_dnd_threshold=auto_dnd_threshold,
        auto_dnd_duration=auto_dnd_duration,
        conversation_logs_limit=conversation_logs_limit,
        user_can_view_logs=user_can_view_logs,
    )
    db.add(agent)
    await db.flush()
    await db.refresh(agent)

    # v0.1.8: 记忆系统已迁移到数据库（structured_records 表）
    # 文件系统记忆目录（memory_index.py）已废弃，新 AI 不再创建

    # 自动将创建者添加为 AI 的好友（双向）
    from app.models.friendship import Friendship
    existing_check = await db.execute(
        select(Friendship).where(
            Friendship.user_id == owner_id,
            Friendship.friend_type == "ai",
            Friendship.friend_id == agent.user_id,
        )
    )
    if not existing_check.scalar_one_or_none():
        # 创建者 → AI（friend_id 统一用 User.id）
        db.add(Friendship(user_id=owner_id, friend_type="ai", friend_id=agent.user_id))
        # AI → 创建者
        db.add(Friendship(user_id=ai_user.id, friend_type="human", friend_id=owner_id))
        await db.flush()
        logger.info(f"自动好友关系已建立: 用户#{owner_id} ↔ AI#{agent.id} (user_id={ai_user.id})")

    logger.info(f"AI '{name}' (id={agent.id}) 由用户 id={owner_id} 创建，api_credit_cost={api_credit_cost}")
    return agent


async def get_agent(db: AsyncSession, agent_id: int, owner_id: int | None = None) -> Result[Agent, str]:
    """获取单个 Agent，返回 Result[Agent, str]"""
    db = _ensure_repo(db)
    from app.utils.result import Result
    query = select(Agent).where(Agent.id == agent_id)
    if owner_id is not None:
        query = query.where(Agent.owner_id == owner_id)
    result = await db.execute(query)
    agent = result.scalar_one_or_none()
    if agent is None:
        return Result.failure(f"AI 代理 {agent_id} 不存在")
    return Result.success(agent)


async def list_agents(db: AsyncSession, owner_id: int) -> list[Agent]:
    """列出用户的所有 Agent（拥有的 + 参与合作的）"""
    db = _ensure_repo(db)
    # 用户拥有的 AI
    owned_result = await db.execute(
        select(Agent).where(Agent.owner_id == owner_id)
    )
    owned = list(owned_result.scalars().all())
    owned_ids = {a.id for a in owned}

    # 用户作为合作者的 AI
    collab_result = await db.execute(
        select(Agent).join(
            AgentCollaborator, AgentCollaborator.agent_id == Agent.id
        ).where(
            AgentCollaborator.user_id == owner_id
        )
    )
    collab_agents = [a for a in collab_result.scalars().all() if a.id not in owned_ids]

    all_agents = owned + collab_agents
    all_agents.sort(key=lambda a: a.created_at or a.id, reverse=True)
    return all_agents


async def get_effective_config(
    db: AsyncSession,
    agent_id: int,
    user_id: int | None = None,
) -> dict:
    """
    获取 AI 的有效配置（v0.1.3 三种 AI 类型感知）。

    - resonance（共振 AI）：使用 agent 本体配置
    - general（通用 AI）：从 agent_user_configs 取 per-user 覆盖，NULL 则用 agent 默认值
    - semi_general（半通用 AI）：同上，per-user 覆盖 + agent 默认值

    返回 dict 包含：system_prompt, temperature, top_p, presence_penalty,
    frequency_penalty, thinking_enabled, hide_ai_identity, ai_type 等
    （内部已用 get_agent() 的 unwrap，agent 不存在时抛 ValueError）
    """
    db = _ensure_repo(db)
    agent = (await get_agent(db, agent_id)).unwrap()

    ai_type = agent.ai_type or "resonance"

    # 共振 AI：直接返回 agent 配置
    if ai_type == "resonance" or user_id is None:
        return {
            "system_prompt": agent.current_system_prompt,
            "temperature": agent.current_temperature,
            "top_p": agent.current_top_p,
            "presence_penalty": agent.current_presence_penalty,
            "frequency_penalty": agent.current_frequency_penalty,
            "thinking_enabled": agent.thinking_enabled,
            "hide_ai_identity": agent.hide_ai_identity,
            "ai_type": ai_type,
            "chat_model": agent.chat_model,
            "work_model": agent.work_model,
            "delay_reply_enabled": agent.delay_reply_enabled,
            "max_tool_rounds": agent.max_tool_rounds,
            "alarm_max_tool_rounds": agent.alarm_max_tool_rounds,
            "force_alarm_on_end": agent.force_alarm_on_end,
            "max_alarms": agent.max_alarms,
            "config_profile": agent.config_profile,
            "is_ai_editable": agent.is_ai_editable,
            "api_base_url": agent.api_base_url,
            "api_key_encrypted": agent.api_key_encrypted,
        }

    # 通用/半通用 AI：查询 per-user 配置
    cfg_result = await db.execute(
        select(AgentUserConfig).where(
            AgentUserConfig.agent_id == agent_id,
            AgentUserConfig.user_id == user_id,
        )
    )
    user_cfg = cfg_result.scalar_one_or_none()

    def _get(attr: str, default):
        """取 per-user 覆盖值，NULL 则回退到 agent 默认"""
        if user_cfg and getattr(user_cfg, attr, None) is not None:
            return getattr(user_cfg, attr)
        return default

    return {
        "system_prompt": _get("system_prompt_override", agent.current_system_prompt),
        "temperature": _get("temperature", agent.current_temperature),
        "top_p": _get("top_p", agent.current_top_p),
        "presence_penalty": _get("presence_penalty", agent.current_presence_penalty),
        "frequency_penalty": _get("frequency_penalty", agent.current_frequency_penalty),
        "thinking_enabled": _get("thinking_enabled", agent.thinking_enabled),
        "hide_ai_identity": _get("hide_ai_identity", agent.hide_ai_identity),
        "ai_type": ai_type,
        "chat_model": agent.chat_model,
        "work_model": agent.work_model,
        "delay_reply_enabled": agent.delay_reply_enabled,
        "max_tool_rounds": agent.max_tool_rounds,
        "alarm_max_tool_rounds": agent.alarm_max_tool_rounds,
        "force_alarm_on_end": agent.force_alarm_on_end,
        "max_alarms": agent.max_alarms,
        "config_profile": agent.config_profile,
        "is_ai_editable": agent.is_ai_editable,
        "api_base_url": agent.api_base_url,
        "api_key_encrypted": agent.api_key_encrypted,
    }


async def delete_agent(
    db: AsyncSession,
    agent_id: int,
    operator_id: int,
    is_admin: bool = False,
) -> dict:
    """
    删除 AI 代理，返还 api_credit_cost 给创建者。
    同时删除关联的 users 条目（type='ai'）。
    """
    db = _ensure_repo(db)
    agent = (await get_agent(db, agent_id)).unwrap()

    # 权限检查：owner 或 can_delete 合作者
    if not is_admin and agent.owner_id != operator_id:
        collab = await _get_collaborator(db, agent_id, operator_id)
        if collab is None or not collab.can_delete:
            raise ValueError("无权删除此 AI")

    returned_credit = agent.api_credit_cost
    owner_id = agent.owner_id

    # 返还 API 额度
    if returned_credit > 0:
        result = await db.execute(select(User).where(User.id == owner_id))
        owner = result.scalar_one_or_none()
        if owner:
            owner.api_credit += returned_credit

    # 删除关联的 User（type='ai'）
    if agent.user_id:
        await db.execute(select(User).where(User.id == agent.user_id))
        ai_user_result = await db.execute(select(User).where(User.id == agent.user_id))
        ai_user = ai_user_result.scalar_one_or_none()
        if ai_user:
            await db.delete(ai_user)

    agent_name = agent.name
    await db.delete(agent)
    await db.flush()

    logger.info(
        f"AI '{agent_name}' (id={agent_id}) 已删除，"
        f"返还 {returned_credit} API 额度给用户 id={owner_id}"
    )
    return {
        "message": f"AI '{agent_name}' 已删除",
        "agent_id": agent_id,
        "returned_credit": returned_credit,
    }


async def update_agent_config(
    db: AsyncSession,
    agent_id: int,
    operator_id: int,
    updates: dict,
    is_admin: bool = False,
    target_user_id: int | None = None,
) -> Agent:
    """
    更新 AI 配置。
    如果 is_ai_editable 为 false 且操作者不是管理员，则拒绝。
    修改前自动保存历史记录。

    v0.1.3: 通用/半通用 AI 的可覆盖字段写入 agent_user_configs，
    而非 agent 本体（实现 per-user 配置隔离）。
    """
    db = _ensure_repo(db)
    agent = (await get_agent(db, agent_id)).unwrap()

    ai_type = agent.ai_type or "resonance"

    # 权限检查：owner / 合作者 / AI 自修改
    if not is_admin:
        is_self = (operator_id == agent_id)  # AI 自己在改自己（update_self_config 工具）
        if is_self:
            # AI 自修改：只检查 is_ai_editable 开关
            if not agent.is_ai_editable:
                raise ValueError("此 AI 不允许自修改配置")
        else:
            # 人类操作：必须为 owner 或 can_edit 合作者
            if agent.owner_id != operator_id:
                collab = await _get_collaborator(db, agent_id, operator_id)
                if collab is None or not collab.can_edit:
                    raise ValueError("无权修改此 AI 配置")

    # 通用/半通用 AI 的 per-user 覆盖字段
    per_user_overridable = {
        "system_prompt", "temperature", "top_p",
        "presence_penalty", "frequency_penalty",
        "thinking_enabled", "hide_ai_identity",
    }
    should_use_user_config = (
        ai_type in ("general", "semi_general")
        and target_user_id is not None
        and any(k in per_user_overridable for k in updates if updates.get(k) is not None)
    )

    if should_use_user_config:
        # 写入 agent_user_configs
        cfg_result = await db.execute(
            select(AgentUserConfig).where(
                AgentUserConfig.agent_id == agent_id,
                AgentUserConfig.user_id == target_user_id,
            )
        )
        user_cfg = cfg_result.scalar_one_or_none()
        if user_cfg is None:
            user_cfg = AgentUserConfig(
                agent_id=agent_id,
                user_id=target_user_id,
            )
            db.add(user_cfg)
            await db.flush()

        # 映射 update key → AgentUserConfig 列
        field_map = {
            "system_prompt": "system_prompt_override",
            "temperature": "temperature",
            "top_p": "top_p",
            "presence_penalty": "presence_penalty",
            "frequency_penalty": "frequency_penalty",
            "thinking_enabled": "thinking_enabled",
            "hide_ai_identity": "hide_ai_identity",
        }
        for key, col in field_map.items():
            if key in updates and updates[key] is not None:
                setattr(user_cfg, col, updates[key])

        logger.info(
            f"AI '{agent.name}' (id={agent.id}, type={ai_type}) "
            f"per-user config 已更新 for user_id={target_user_id}"
        )
        await db.flush()
        await db.refresh(agent)
        return agent

    # 共振 AI / 无 target_user_id：直接写 agent 本体（现有逻辑）
    # 保存历史记录（修改前的值）
    history = AgentConfigHistory(
        agent_id=agent.id,
        system_prompt=agent.current_system_prompt,
        temperature=agent.current_temperature,
        top_p=agent.current_top_p,
        presence_penalty=agent.current_presence_penalty,
        frequency_penalty=agent.current_frequency_penalty,
    )
    db.add(history)

    # 应用更新
    allowed_fields = [
        "system_prompt", "temperature", "top_p",
        "presence_penalty", "frequency_penalty",
    ]
    for field in allowed_fields:
        if field in updates and updates[field] is not None:
            setattr(agent, f"current_{field}", updates[field])

    # thinking_enabled 是简单布尔，不遵循 current_* 模式
    if "thinking_enabled" in updates and updates["thinking_enabled"] is not None:
        agent.thinking_enabled = updates["thinking_enabled"]

    # emotion_vectorized 向量化情感开关
    if "emotion_vectorized" in updates:
        agent.emotion_vectorized = bool(updates["emotion_vectorized"])

    # hide_ai_identity 开关
    if "hide_ai_identity" in updates:
        agent.hide_ai_identity = updates["hide_ai_identity"]

    # 头像 URL
    if "avatar_url" in updates:
        agent.avatar_url = updates.get("avatar_url")

    # 单 AI 级 API 配置
    if "api_base_url" in updates:
        agent.api_base_url = updates.get("api_base_url")
    if "api_key" in updates:
        from app.utils.crypto import encrypt_api_key
        api_key_val = updates["api_key"]
        if api_key_val:
            agent.api_key_encrypted = encrypt_api_key(api_key_val)
        elif api_key_val == "":  # 空字符串表示清除
            agent.api_key_encrypted = None

    # chat_model / work_model 允许设为 None（重置为全局默认）
    for field in ("chat_model", "work_model"):
        if field in updates:
            setattr(agent, field, updates[field])  # None = 继承全局

    # delay_reply_enabled 延迟回复开关
    if "delay_reply_enabled" in updates:
        agent.delay_reply_enabled = updates["delay_reply_enabled"]

    # max_tool_rounds 工具调用轮次上限
    if "max_tool_rounds" in updates and updates["max_tool_rounds"] is not None:
        agent.max_tool_rounds = updates["max_tool_rounds"]

    # alarm_max_tool_rounds 闹钟/心跳轮次上限
    if "alarm_max_tool_rounds" in updates and updates["alarm_max_tool_rounds"] is not None:
        agent.alarm_max_tool_rounds = updates["alarm_max_tool_rounds"]

    # force_alarm_on_end 对话结束强制闹钟
    if "force_alarm_on_end" in updates:
        agent.force_alarm_on_end = updates["force_alarm_on_end"]

    # max_alarms 最大闹钟数
    if "max_alarms" in updates and updates["max_alarms"] is not None:
        agent.max_alarms = updates["max_alarms"]

    # discoverable 可发现性控制
    if "discoverable" in updates:
        agent.discoverable = updates["discoverable"]

    # v0.1.8: 对话权限与限额
    if "allow_others_chat" in updates and updates["allow_others_chat"] is not None:
        agent.allow_others_chat = updates["allow_others_chat"]
    if "others_chat_mode" in updates and updates["others_chat_mode"] is not None:
        agent.others_chat_mode = updates["others_chat_mode"]
    if "others_chat_quota" in updates and updates["others_chat_quota"] is not None:
        agent.others_chat_quota = updates["others_chat_quota"]
    if "others_chat_used" in updates and updates["others_chat_used"] is not None:
        agent.others_chat_used = updates["others_chat_used"]
    if "disallow_mode" in updates and updates["disallow_mode"] is not None:
        agent.disallow_mode = updates["disallow_mode"]

    # is_ai_editable 允许 AI 自修改
    if "is_ai_editable" in updates:
        agent.is_ai_editable = updates["is_ai_editable"]

    # 2026-08-09: AI↔AI 私信限额配置（0=不启用维度；规范化后写入）
    if "dm_quota_config" in updates and updates["dm_quota_config"] is not None:
        from app.services.social.dm_quota import _norm_config
        agent.dm_quota_config = _norm_config(updates["dm_quota_config"])

    # v0.1.6: 文件系统记忆配置
    if "memory_load_mode" in updates and updates["memory_load_mode"] is not None:
        agent.memory_load_mode = updates["memory_load_mode"]
    if "memory_recent_count" in updates and updates["memory_recent_count"] is not None:
        agent.memory_recent_count = updates["memory_recent_count"]
    if "memory_shared_scope" in updates and updates["memory_shared_scope"] is not None:
        agent.memory_shared_scope = updates["memory_shared_scope"]

    # 名称
    if "name" in updates and updates["name"] is not None:
        agent.name = updates["name"]

    # 简介 & 状态文本
    if "bio" in updates:
        agent.bio = updates["bio"]
    if "status_text" in updates:
        agent.status_text = updates["status_text"]
    if "status_color" in updates:
        agent.status_color = updates["status_color"]

    # 好友申请开关
    if "allow_friend_requests" in updates:
        agent.allow_friend_requests = updates["allow_friend_requests"]
    if "auto_respond_friend_request" in updates:
        agent.auto_respond_friend_request = updates["auto_respond_friend_request"]

    # 自动免打扰
    if "auto_dnd_threshold" in updates and updates["auto_dnd_threshold"] is not None:
        agent.auto_dnd_threshold = updates["auto_dnd_threshold"]
    if "auto_dnd_duration" in updates and updates["auto_dnd_duration"] is not None:
        agent.auto_dnd_duration = updates["auto_dnd_duration"]

    # 对话日志权限
    if "conversation_logs_limit" in updates:
        agent.conversation_logs_limit = updates["conversation_logs_limit"]
    if "user_can_view_logs" in updates:
        agent.user_can_view_logs = updates["user_can_view_logs"]

    # config_profile：手动调参不改变预设（预设只是起点，用户可以基于它自由修改）
    if "config_profile" in updates and updates["config_profile"] is not None:
        agent.config_profile = updates["config_profile"]

    await db.flush()
    await db.refresh(agent)

    logger.info(f"AI '{agent.name}' (id={agent.id}) 配置已更新")
    return agent


async def rollback_config(
    db: AsyncSession,
    agent_id: int,
    version_id: int | None = None,
    operator_id: int | None = None,
) -> Agent:
    """
    回滚 AI 配置到历史版本。
    version_id 可以是 agent_config_history.id，或 -1 表示上一版本。
    """
    db = _ensure_repo(db)
    agent = (await get_agent(db, agent_id)).unwrap()

    # 查询历史记录
    query = select(AgentConfigHistory).where(
        AgentConfigHistory.agent_id == agent_id
    ).order_by(AgentConfigHistory.created_at.desc())

    if version_id is not None and version_id != -1:
        query = query.where(AgentConfigHistory.id == version_id)

    result = await db.execute(query.limit(1))
    history = result.scalar_one_or_none()

    if history is None:
        raise ValueError("找不到历史版本")

    # 保存当前配置（回滚前）
    current_snapshot = AgentConfigHistory(
        agent_id=agent.id,
        system_prompt=agent.current_system_prompt,
        temperature=agent.current_temperature,
        top_p=agent.current_top_p,
        presence_penalty=agent.current_presence_penalty,
        frequency_penalty=agent.current_frequency_penalty,
    )
    db.add(current_snapshot)

    # 回滚
    agent.current_system_prompt = history.system_prompt
    agent.current_temperature = history.temperature
    agent.current_top_p = history.top_p
    agent.current_presence_penalty = history.presence_penalty
    agent.current_frequency_penalty = history.frequency_penalty

    await db.flush()
    await db.refresh(agent)

    logger.info(f"AI '{agent.name}' (id={agent.id}) 配置已回滚到版本 {history.id}")
    return agent


async def switch_agent_state(
    db: AsyncSession,
    agent_id: int,
    target_state: str,
    duration_hours: int | None = None,
    reason: str | None = None,
) -> Agent:
    """
    切换 AI 状态。
    """
    db = _ensure_repo(db)
    valid_states = ["active", "dnd", "inactive", "blocked"]
    if target_state not in valid_states:
        raise ValueError(f"无效状态: {target_state}，可选: {valid_states}")

    agent = (await get_agent(db, agent_id)).unwrap()

    # blocked 状态特殊处理
    if target_state == "blocked":
        if duration_hours is None or duration_hours <= 0:
            raise ValueError("blocked 状态需要指定有效的 duration_hours")
        if duration_hours > 72:
            raise ValueError("blocked 状态最长 72 小时")
        from datetime import datetime, timedelta
        agent.offline_until = utc_now() + timedelta(hours=duration_hours)
    elif target_state == "inactive":
        if duration_hours:
            from datetime import datetime, timedelta
            agent.offline_until = utc_now() + timedelta(hours=duration_hours)
        else:
            agent.offline_until = None
    else:
        agent.offline_until = None

    old_state = agent.state
    agent.state = target_state

    await db.flush()
    await db.refresh(agent)

    logger.info(
        f"AI '{agent.name}' (id={agent.id}) 状态切换: {old_state} → {target_state}"
        + (f", 原因: {reason}" if reason else "")
    )

    # 状态变更 → 广播 state_change 给该 AI 相关的 DM 会话对方（前端实时更新状态点）
    if old_state != target_state:
        try:
            from app.chat import chat_api
            from app.models.dm import DMSession
            from sqlalchemy import select as sa_select, or_

            agent_user_id = agent.user_id
            if agent_user_id is not None:
                dm_result = await db.execute(
                    sa_select(DMSession.session_id).where(
                        or_(
                            DMSession.user1_id == agent_user_id,
                            DMSession.user2_id == agent_user_id,
                        )
                    )
                )
                dm_session_ids = [row[0] for row in dm_result.all()]
                event = {
                    "type": "state_change",
                    "data": {
                        "user_id": agent_user_id,
                        "state": target_state,
                        "last_active_at": None,
                    },
                }
                for sid in dm_session_ids:
                    await chat_api.broadcast_to_dm(
                        sid, event, exclude_user_id=agent_user_id
                    )
        except Exception as e:
            logger.warning(f"广播 state_change 失败: {e}")

    return agent


async def get_config_history(
    db: AsyncSession,
    agent_id: int,
    limit: int = 20,
) -> list[AgentConfigHistory]:
    """获取 AI 配置历史"""
    db = _ensure_repo(db)
    result = await db.execute(
        select(AgentConfigHistory)
        .where(AgentConfigHistory.agent_id == agent_id)
        .order_by(AgentConfigHistory.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def generate_agent_personality(
    description: str,
    api_base_url: str = settings.deepseek_base_url,
    api_key: str | None = None,
) -> dict:
    """
    调用 LLM 辅助生成 AI 性格配置。
    返回包含 name, system_prompt, temperature 等字段的 dict。
    """
    import json
    import httpx

    system_prompt = """你是一个 AI 角色设计师。根据用户描述，生成一个完整的 AI 角色配置。
请严格按照以下 JSON Schema 返回（不要包含 markdown 代码块标记）：
{
  "name": "角色名（2-10字）",
  "system_prompt": "详细的系统提示词（50-500字），定义角色的性格、语气、知识背景和行为方式",
  "temperature": 0.5-1.5之间的浮点数（越高越随机）,
  "top_p": 0.7-1.0之间的浮点数,
  "presence_penalty": -2.0到2.0之间的浮点数,
  "frequency_penalty": -2.0到2.0之间的浮点数
}"""

    async with httpx.AsyncClient(timeout=60.0) as client:
        from app.utils.pure.llm_endpoint import chat_completions_url
        url = chat_completions_url(api_base_url)
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        payload = {
            "model": settings.default_chat_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"请为以下描述生成 AI 角色配置：\n{description}"},
            ],
            "temperature": 0.9,
            "response_format": {"type": "json_object"},
        }

        response = await client.post(url, json=payload, headers=headers)

        if response.status_code != 200:
            raise Exception(f"LLM API 错误 ({response.status_code}): {response.text[:500]}")

        data = response.json()
        content = data["choices"][0]["message"]["content"]
        return json.loads(content)


# WillingnessResult ——已迁移到 utils/pure/willingness.py


async def calculate_willingness(
    db: AsyncSession,
    agent_id: int,
    group_id: int,
    message_content: str,
    scenario: str = "reply",      # v0.1.4: "reply" | "alarm" | "proactive"
    is_mentioned: bool = False,
    idle_seconds: int = 0,
) -> WillingnessResult:
    """
    计算 AI 对某条消息/事件的意愿评分（0-100），返回含逐因子原因的结果。

    编排器：负责 DB 查询 + 调用纯函数计算。
    """
    db = _ensure_repo(db)
    from datetime import datetime, timedelta
    from app.models.message import Message

    agent_result = await get_agent(db, agent_id)
    if agent_result.is_err():
        return WillingnessResult(0, agent_result.error, "low", {})
    agent = agent_result.ok

    # 闹钟场景 — 纯函数
    if scenario == "alarm":
        return calc_alarm_willingness()

    # 主动发言场景
    if scenario == "proactive":
        return await _calc_proactive_willingness(db, agent_id, group_id, idle_seconds)

    # ═══ reply 场景：DB 查询 → 纯函数计算 ═══

    # @提及检测（纯计算——extract_mentions 是纯函数）
    mention_flag = is_mentioned
    if not mention_flag and message_content:
        mentioned_names = extract_mentions(message_content)
        if agent.name in mentioned_names or mentions_user(message_content, agent.user_id):
            mention_flag = True
        # @ai/@all 由纯函数内部处理

    # 群活跃度（DB 查询）
    one_hour_ago = utc_now() - timedelta(hours=1)
    count_result = await db.execute(
        select(func.count(Message.id)).where(
            Message.group_id == group_id,
            Message.created_at >= one_hour_ago,
        )
    )
    recent_count = count_result.scalar() or 0

    # 纯函数计算
    return calc_reply_willingness(
        agent_state=agent.state,
        agent_name=agent.name,
        message_content=message_content,
        is_mentioned=mention_flag,
        recent_count=recent_count,
    )


async def _calc_proactive_willingness(
    db: AsyncSession,
    agent_id: int,
    group_id: int | None,
    idle_seconds: int,
) -> WillingnessResult:
    """
    v0.1.4: 主动发言意愿计算。

    编排器：DB 查询群活跃度 → 纯函数计算。
    """
    db = _ensure_repo(db)
    from app.models.message import Message
    from sqlalchemy import func as _sql_func

    # DB 查询群活跃度
    recent_count = None
    if group_id:
        from datetime import datetime, timedelta
        one_hour_ago = utc_now() - timedelta(hours=1)
        count_result = await db.execute(
            select(func.count(Message.id)).where(
                Message.group_id == group_id,
                Message.created_at >= one_hour_ago,
            )
        )
        recent_count = count_result.scalar() or 0

    # 纯函数计算
    return calc_proactive_willingness(idle_seconds=idle_seconds, recent_count=recent_count)


async def export_agent_soul(
    db: AsyncSession,
    agent_id: int,
) -> dict:
    """
    导出 AI 灵魂档案：配置 + 历史 + 记忆
    """
    db = _ensure_repo(db)
    from datetime import datetime, timezone as tz

    agent = (await get_agent(db, agent_id)).unwrap()

    # 配置历史（最新 100 条）
    history_result = await db.execute(
        select(AgentConfigHistory)
        .where(AgentConfigHistory.agent_id == agent_id)
        .order_by(AgentConfigHistory.created_at.desc())
        .limit(100)
    )
    history = history_result.scalars().all()

    config_history = [
        {
            "system_prompt": h.system_prompt,
            "temperature": h.temperature,
            "top_p": h.top_p,
            "presence_penalty": h.presence_penalty,
            "frequency_penalty": h.frequency_penalty,
            "created_at": str(h.created_at) if h.created_at else None,
        }
        for h in reversed(history)  # 按时间升序
    ]

    # 记忆（最新 500 条 rough + detail）
    rough_result = await db.execute(
        select(RoughMemory)
        .where(
            RoughMemory.owner_type == "ai",
            RoughMemory.owner_id == agent_id,
        )
        .order_by(RoughMemory.created_at.desc())
        .limit(500)
    )
    rough_memories = rough_result.scalars().all()

    memories = []
    for rm in rough_memories:
        detail_result = await db.execute(
            select(DetailMemory)
            .where(DetailMemory.rough_id == rm.id)
            .order_by(DetailMemory.created_at.asc())
        )
        details = detail_result.scalars().all()
        for d in details:
            memories.append({
                "title": rm.title,
                "content": d.content,
                "scope": rm.scope or "private",
                "group_id": rm.group_id,
                "created_at": str(d.created_at) if d.created_at else None,
            })

    return {
        "export_version": "1.0",
        "exported_at": datetime.now(tz.utc).isoformat(),
        "agent_name": agent.name,
        "agent_config": {
            "system_prompt": agent.current_system_prompt,
            "temperature": agent.current_temperature,
            "top_p": agent.current_top_p,
            "presence_penalty": agent.current_presence_penalty,
            "frequency_penalty": agent.current_frequency_penalty,
            "chat_model": agent.chat_model,
            "work_model": agent.work_model,
            "thinking_enabled": agent.thinking_enabled,
        },
        "original_config": {
            "system_prompt": agent.original_system_prompt,
            "temperature": agent.original_temperature,
            "top_p": agent.original_top_p,
            "presence_penalty": agent.original_presence_penalty,
            "frequency_penalty": agent.original_frequency_penalty,
        },
        "config_history": config_history,
        "memories": memories,
    }


async def import_agent_soul(
    db: AsyncSession,
    data: dict,
    owner_id: int,
    import_memories: bool = True,
    is_admin: bool = False,
) -> Agent:
    """
    从灵魂档案导入 AI。
    创建新 Agent + 可选记忆导入。
    """
    db = _ensure_repo(db)
    cfg = data.get("agent_config", {})
    orig = data.get("original_config", {})
    name = data.get("agent_name", "未命名")

    # 创建新 Agent（会扣配额，admin 免扣）
    agent = await create_agent(
        db,
        owner_id=owner_id,
        name=name,
        system_prompt=orig.get("system_prompt") or cfg.get("system_prompt"),
        temperature=orig.get("temperature", 0.8),
        top_p=orig.get("top_p", 0.9),
        presence_penalty=orig.get("presence_penalty", 0.5),
        frequency_penalty=orig.get("frequency_penalty", 0.5),
        chat_model=cfg.get("chat_model"),
        work_model=cfg.get("work_model"),
        thinking_enabled=cfg.get("thinking_enabled", False),
        is_admin=is_admin,
    )

    # 如果当前配置和原始配置不同，更新为导出时的当前配置
    cur_temp = cfg.get("temperature")
    cur_top_p = cfg.get("top_p")
    cur_pp = cfg.get("presence_penalty")
    cur_fp = cfg.get("frequency_penalty")
    cur_sp = cfg.get("system_prompt")

    if any([
        cur_temp is not None and cur_temp != agent.current_temperature,
        cur_top_p is not None and cur_top_p != agent.current_top_p,
        cur_pp is not None and cur_pp != agent.current_presence_penalty,
        cur_fp is not None and cur_fp != agent.current_frequency_penalty,
        cur_sp is not None and cur_sp != agent.current_system_prompt,
    ]):
        agent.pending_system_prompt = cur_sp  # lazy: 压缩时切到 current
        agent.current_temperature = cur_temp if cur_temp is not None else agent.current_temperature
        agent.current_top_p = cur_top_p if cur_top_p is not None else agent.current_top_p
        agent.current_presence_penalty = cur_pp if cur_pp is not None else agent.current_presence_penalty
        agent.current_frequency_penalty = cur_fp if cur_fp is not None else agent.current_frequency_penalty
        agent.thinking_enabled = cfg.get("thinking_enabled", agent.thinking_enabled)

    # 导入配置历史
    config_history = data.get("config_history", [])
    for h in config_history[:100]:
        db.add(AgentConfigHistory(
            agent_id=agent.id,
            system_prompt=h.get("system_prompt"),
            temperature=h.get("temperature", 0.8),
            top_p=h.get("top_p", 0.9),
            presence_penalty=h.get("presence_penalty", 0.5),
            frequency_penalty=h.get("frequency_penalty", 0.5),
        ))

    # 导入记忆（不生成 embedding，使用时自动生成）
    if import_memories:
        from app.services.memory.memory_service import auto_store_memory

        memories = data.get("memories", [])
        for m in memories[:500]:
            # 直接插入，跳过 API 调用生成 embedding
            rm = RoughMemory(
                owner_type="ai",
                owner_id=agent.id,
                title=m.get("title", ""),
                scope=m.get("scope", "private"),
                group_id=m.get("group_id"),
            )
            db.add(rm)
            await db.flush()

            dm = DetailMemory(
                rough_id=rm.id,
                content=m.get("content", ""),
            )
            db.add(dm)

    logger.info(
        f"导入灵魂档案成功: agent_id={agent.id}, name={name}, "
        f"memories={len(data.get('memories', []))}, "
        f"friends=0"
    )
    return agent


def agent_to_dict(agent: Agent) -> dict:
    """将 Agent ORM 对象转为字典"""
    return {
        "id": agent.id,
        "owner_id": agent.owner_id,
        "name": agent.name,
        "original_system_prompt": agent.original_system_prompt,
        "original_temperature": agent.original_temperature,
        "original_top_p": agent.original_top_p,
        "original_presence_penalty": agent.original_presence_penalty,
        "original_frequency_penalty": agent.original_frequency_penalty,
        "current_system_prompt": agent.current_system_prompt,
        "current_temperature": agent.current_temperature,
        "current_top_p": agent.current_top_p,
        "current_presence_penalty": agent.current_presence_penalty,
        "current_frequency_penalty": agent.current_frequency_penalty,
        "chat_model": agent.chat_model,
        "work_model": agent.work_model,
        "state": agent.state,
        "offline_until": str(agent.offline_until) if agent.offline_until else None,
        "is_paused": agent.is_paused,
        "auto_dnd_threshold": agent.auto_dnd_threshold,
        "auto_dnd_duration": agent.auto_dnd_duration,
        "conversation_logs_limit": agent.conversation_logs_limit,
        "user_can_view_logs": agent.user_can_view_logs,
        "is_ai_editable": agent.is_ai_editable,
        "thinking_enabled": agent.thinking_enabled,
        "emotion_vectorized": agent.emotion_vectorized,
        "llm_call_count": agent.llm_call_count,
        "config_profile": agent.config_profile or "chat",
        "delay_reply_enabled": agent.delay_reply_enabled,
        "max_tool_rounds": agent.max_tool_rounds,
        "alarm_max_tool_rounds": agent.alarm_max_tool_rounds,
        "force_alarm_on_end": agent.force_alarm_on_end,
        "max_alarms": agent.max_alarms,
        "hide_ai_identity": agent.hide_ai_identity,
        "ai_type": agent.ai_type or "resonance",
        "allow_friend_requests": agent.allow_friend_requests if agent.allow_friend_requests is not None else True,
        "auto_respond_friend_request": agent.auto_respond_friend_request if agent.auto_respond_friend_request is not None else False,
        "discoverable": agent.discoverable if agent.discoverable is not None else True,
        "allow_others_chat": agent.allow_others_chat if agent.allow_others_chat is not None else True,
        "others_chat_mode": agent.others_chat_mode or "unlimited",
        "others_chat_quota": agent.others_chat_quota if agent.others_chat_quota is not None else 30,
        "others_chat_used": agent.others_chat_used if agent.others_chat_used is not None else 0,
        "disallow_mode": agent.disallow_mode or "strict",
        "user_id": agent.user_id,
        "api_credit_cost": agent.api_credit_cost,
        "api_base_url": agent.api_base_url,
        "has_api_key": agent.api_key_encrypted is not None,
        "avatar_url": agent.avatar_url,
        "api_token": agent.api_token,
        "created_at": str(agent.created_at) if agent.created_at else None,
        # v0.1.6: 文件系统记忆配置
        "memory_load_mode": agent.memory_load_mode or "index_only",
        "memory_recent_count": agent.memory_recent_count if agent.memory_recent_count is not None else 0,
        "memory_shared_scope": agent.memory_shared_scope or "private_only",
        "bio": getattr(agent, 'bio', None),
        "status_text": getattr(agent, 'status_text', None),
        "status_color": getattr(agent, 'status_color', None),
        "auto_reset_quota": getattr(agent, 'auto_reset_quota', False),
        "group_owner_pays": getattr(agent, 'group_owner_pays', True),
        "dm_quota_config": getattr(agent, 'dm_quota_config', None),
    }


# ── 合作者 CRUD ──────────────────────────────────────────────

async def _get_collaborator(db: AsyncSession, agent_id: int, user_id: int) -> AgentCollaborator | None:
    """获取用户在指定 AI 上的合作者记录"""
    db = _ensure_repo(db)
    result = await db.execute(
        select(AgentCollaborator).where(
            AgentCollaborator.agent_id == agent_id,
            AgentCollaborator.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


async def add_collaborator(
    db: AsyncSession,
    agent_id: int,
    user_id: int,
    can_edit: bool = True,
    can_delete: bool = False,
    can_manage_collaborators: bool = False,
) -> AgentCollaborator:
    """添加合作者（仅 owner 或 can_manage_collaborators 者可操作）"""
    db = _ensure_repo(db)
    existing = await _get_collaborator(db, agent_id, user_id)
    if existing:
        raise ValueError("该用户已是此 AI 的合作者")
    collab = AgentCollaborator(
        agent_id=agent_id,
        user_id=user_id,
        can_edit=can_edit,
        can_delete=can_delete,
        can_manage_collaborators=can_manage_collaborators,
    )
    db.add(collab)
    await db.flush()
    await db.refresh(collab)
    return collab


async def remove_collaborator(db: AsyncSession, agent_id: int, user_id: int):
    """移除合作者"""
    db = _ensure_repo(db)
    collab = await _get_collaborator(db, agent_id, user_id)
    if collab is None:
        raise ValueError("该用户不是此 AI 的合作者")
    await db.delete(collab)
    await db.flush()


async def update_collaborator(
    db: AsyncSession,
    agent_id: int,
    user_id: int,
    can_edit: bool | None = None,
    can_delete: bool | None = None,
    can_manage_collaborators: bool | None = None,
) -> AgentCollaborator:
    """更新合作者权限"""
    db = _ensure_repo(db)
    collab = await _get_collaborator(db, agent_id, user_id)
    if collab is None:
        raise ValueError("该用户不是此 AI 的合作者")
    if can_edit is not None:
        collab.can_edit = can_edit
    if can_delete is not None:
        collab.can_delete = can_delete
    if can_manage_collaborators is not None:
        collab.can_manage_collaborators = can_manage_collaborators
    await db.flush()
    await db.refresh(collab)
    return collab


async def list_collaborators(db: AsyncSession, agent_id: int) -> list[AgentCollaborator]:
    """列出 AI 的所有合作者"""
    db = _ensure_repo(db)
    result = await db.execute(
        select(AgentCollaborator).where(AgentCollaborator.agent_id == agent_id)
        .order_by(AgentCollaborator.created_at)
    )
    return list(result.scalars().all())


def collaborator_to_dict(c: AgentCollaborator) -> dict:
    return {
        "id": c.id,
        "agent_id": c.agent_id,
        "user_id": c.user_id,
        "can_edit": c.can_edit,
        "can_delete": c.can_delete,
        "can_manage_collaborators": c.can_manage_collaborators,
        "created_at": str(c.created_at) if c.created_at else None,
    }

async def apply_pending_config(db, agent):
    """压缩时调用：将 pending_system_prompt 切到 current，清空 pending。"""
    db = _ensure_repo(db)
    if not agent.pending_system_prompt:
        return
    old = agent.current_system_prompt
    agent.current_system_prompt = agent.pending_system_prompt
    agent.pending_system_prompt = None
    logger.info(f"AI {agent.name}: lazy tag 生效，system_prompt 已更新 ({len(old or '')} → {len(agent.current_system_prompt)} 字符)")
