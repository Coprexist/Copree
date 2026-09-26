"""
AI 代理模型
"""
from sqlalchemy import (
    Column, Integer, String, Boolean, Float, Text, DateTime,
    ForeignKey, Index, UniqueConstraint, func,
)
from app.db_providers import json_column
from app.database import Base


class Agent(Base):
    __tablename__ = "agents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(50), nullable=False)

    # 原始配置（管理员设定，不可被 AI 覆盖）
    original_system_prompt = Column(Text)
    original_temperature = Column(Float, default=0.8)
    original_top_p = Column(Float, default=0.9)
    original_presence_penalty = Column(Float, default=0.5)
    original_frequency_penalty = Column(Float, default=0.5)

    # 当前配置（AI 可自修改）
    current_system_prompt = Column(Text)
    pending_system_prompt = Column(Text, nullable=True)  # AI自修改暂存，压缩时切到current
    # 能力懒加载版本（2026-08-06）：{source: version}
    cap_known_versions = Column(json_column(), default=dict, comment="能力源告知进度（已注入变更通知的版本）")
    cap_effective_versions = Column(json_column(), default=dict, comment="能力源生效进度（请求实际使用的工具定义版本，compact 时更新）")
    current_temperature = Column(Float)
    current_top_p = Column(Float)
    current_presence_penalty = Column(Float)
    current_frequency_penalty = Column(Float)

    # 模型选择（NULL = 继承全局默认）
    chat_model = Column(String(50))
    work_model = Column(String(50))

    # 状态机
    state = Column(String(20), default="active")  # active|dnd|inactive|blocked
    offline_until = Column(DateTime)

    # 全局暂停通知（任务期间暂存所有群聊消息）
    is_paused = Column(Boolean, default=False)

    # 意愿评分 + 自动免打扰配置
    auto_dnd_threshold = Column(Integer, default=20)  # 低于此分自动开 DND
    auto_dnd_duration = Column(Integer, default=5)    # 自动 DND 时长（分钟）

    # 是否允许 AI 自修改
    is_ai_editable = Column(Boolean, default=True)

    # 深度推理模式（DeepSeek thinking），AI 可自行切换
    thinking_enabled = Column(Boolean, default=False)

    # 三档 AI 配置：chat / immersive / digital_life（选一个作为起点，之后可自由调参）
    config_profile = Column(String(20), default="chat")

    # AI 在 users 表中的身份（统一 ID 空间，用于私信等场景）
    user_id = Column(Integer, ForeignKey("users.id"))

    # 对话日志：此 AI 的保留上限（NULL=使用全局 max_conversation_logs）
    conversation_logs_limit = Column(Integer, nullable=True)
    # 对话日志：用户是否可查看此 AI 的日志（NULL=使用全局 default_user_log_access）
    user_can_view_logs = Column(Boolean, nullable=True)

    # API 调用额度成本（创建时从用户 api_credit 扣除，删除时返还）
    api_credit_cost = Column(Integer, default=0)

    # 单 AI 级 API 配置覆盖（NULL = 继承用户全局设置）
    api_base_url = Column(Text)
    api_key_encrypted = Column(Text)

    # 延迟回复功能开关（NULL=继承全局默认，False=关闭，True=开启）
    delay_reply_enabled = Column(Boolean, nullable=True, comment="延迟回复功能开关，NULL=继承全局默认")

    # 单次回复最大工具调用轮次（3 档预设：chat=2 / immersive=4 / digital_life=10）
    max_tool_rounds = Column(Integer, default=5)

    # 闹钟/心跳最大工具调用轮次（独立于普通回复，默认更高以支持深度自主任务）
    alarm_max_tool_rounds = Column(Integer, default=10)

    # 对话结束时是否强制要求 AI 设定闹钟（数字生命档默认开启，防止"睡死"）
    force_alarm_on_end = Column(Boolean, default=False)

    # AI 最多可设多少个活跃闹钟（心跳节奏的边界）
    max_alarms = Column(Integer, default=10)

    # 系统提醒额外轮次模式: every_time(每次都不计) | once(仅一次) | off(计入配额)
    reminder_grace = Column(String(10), default="every_time")

    # 隐藏 AI 身份（开启后系统提示词不包含"你是 AI"相关表述）
    hide_ai_identity = Column(Boolean, default=False)

    # 好友与社交控制（v0.1.5）
    allow_friend_requests = Column(Boolean, default=True, comment="是否允许接收好友申请")
    auto_respond_friend_request = Column(Boolean, default=False, comment="收到好友申请时是否自动触发 API 响应")
    discoverable = Column(Boolean, default=True, comment="是否允许他人发现与查找此 AI")

    # AI 类型 (v0.1.3): general(通用) | semi_general(半通用) | resonance(共振, 默认)
    ai_type = Column(String(20), default="resonance")

    # ── 对话权限与限额 (v0.1.8) ──
    allow_others_chat = Column(Boolean, default=True, comment="是否允许非主人触发此 AI 对话")
    others_chat_mode = Column(String(20), default="unlimited", comment="允许时的模式: unlimited | quota")
    others_chat_quota = Column(Integer, default=30, comment="配额上限（触发次数），仅 quota 模式生效")
    others_chat_used = Column(Integer, default=0, comment="当前已使用次数（可重置）")
    disallow_mode = Column(String(20), default="strict", comment="禁止时的模式: strict | own_key")

    # ── 自动重置配额 (v1.1.0) ──
    auto_reset_quota = Column(Boolean, default=False, comment="每次用户 DM 时自动重置配额计数")

    # AI↔AI 私信限额（2026-08-09）：创建者在配置页设置，0=不启用该维度
    # 格式: {"send": {"daily": 20, "weekly": 0, "creator_chat": 0}, "receive": {...}}
    dm_quota_config = Column(json_column(), default=dict, comment="AI↔AI 私信限额配置: send/receive 各含 daily/weekly/creator_chat 上限（0=不限）")
    # 格式: {"send": {"daily_count": 0, "weekly_count": 0, "creator_chat_count": 0, "daily_anchor": "2026-08-09", "weekly_anchor": "2026-08-04"}, "receive": {...}}
    dm_quota_state = Column(json_column(), default=dict, comment="AI↔AI 私信限额计数（日历周期自动重置；创建者发消息时 creator_chat 计数清零）")
    # ── 配额白名单 (v1.1.0): JSONB 数组 [{type:"group"|"user", id:int}, ...] ──
    quota_whitelist = Column(json_column(), default=list, comment="不消耗配额的白名单实体列表")
    # ── 群主支付 (v1.1.0): 群聊 AI 消息默认由群主付费 ──
    group_owner_pays = Column(Boolean, default=True, comment="群聊中 AI 消息是否由群主付费")

    # ── 文件系统记忆配置 (v0.1.6) ──
    # 记忆加载模式: index_only(仅索引) | index_plus_recent(索引+最近N篇内容) | index_plus_semantic(索引+语义检索)
    memory_load_mode = Column(String(30), default="index_only")
    # index_plus_recent 模式下加载最近 N 个文件的完整内容
    memory_recent_count = Column(Integer, default=0)
    # 共享记忆范围: private_only | private_plus_shared_by_user | private_plus_shared_all
    memory_shared_scope = Column(String(30), default="private_only")

    # 最近意愿评分和原因 (v0.1.3)
    last_willingness_score = Column(Integer, nullable=True)
    last_willingness_reason = Column(Text, nullable=True)

    # 头像 URL
    avatar_url = Column(Text)

    # 个人简介（创建者/合作者可编辑，展示在资料卡中）
    bio = Column(Text, nullable=True, comment="AI 简介")

    # 自定义状态文本（AI 可通过工具自行设置，展示在资料卡和消息旁）
    status_text = Column(String(100), nullable=True, comment="自定义状态文本")
    status_color = Column(String(20), nullable=True, comment="状态文字颜色(hex)")

    # API Token（供外部调用该 AI）
    api_token = Column(String(64))

    # 状态栈 — AI 跨任务上下文追踪（v0.2.1）
    # JSONB 数组，每个元素 {id, type, context_ref, why, doing, todo, plan, journal, created_at, status}
    state_stack = Column(json_column(), default=list)

    # 情感向量化（v0.3.2）：勾选后情感用 Plutchik 8 轴向量（更拟人）；不勾退回文字心情描述
    emotion_vectorized = Column(Boolean, default=False)

    # AI 调用总次数（v0.3.2）：情感/记忆衰减的时间尺度（分状态帧计数在 state_stack 帧内）
    llm_call_count = Column(Integer, default=0)

    # 跨状态便签（v0.3.6）：临时、有时效的跨会话留言。时效刻度就是 llm_call_count ——
    # 写下后 40 次 API 调用内有效（只决定能不能投递）；投进某会话后固化在它的上下文里
    cross_state_notes = Column(json_column(), default=list)

    # 状态栈摘要长度上限（默认 500，AI 配置页可改；最新帧必保完整）
    state_stack_max_chars = Column(Integer, default=500)

    created_at = Column(DateTime, server_default=func.now())


class AgentUserConfig(Base):
    """per-user AI 配置覆盖（通用/半通用 AI 专用）
    每个(user_id, agent_id)对存储该用户对此 AI 的个性化配置。
    NULL 值表示继承 AI 默认值。
    """
    __tablename__ = "agent_user_configs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_id = Column(Integer, ForeignKey("agents.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    # 以下均为覆盖值，NULL = 使用 agent 本体配置
    temperature = Column(Float, nullable=True)
    top_p = Column(Float, nullable=True)
    presence_penalty = Column(Float, nullable=True)
    frequency_penalty = Column(Float, nullable=True)
    thinking_enabled = Column(Boolean, nullable=True)
    hide_ai_identity = Column(Boolean, nullable=True)
    system_prompt_override = Column(Text, nullable=True)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("agent_id", "user_id", name="uq_agent_user_config"),
    )


class AgentConfigHistory(Base):
    """AI 配置历史记录（用于回滚）"""
    __tablename__ = "agent_config_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_id = Column(Integer, ForeignKey("agents.id", ondelete="CASCADE"), nullable=False)

    system_prompt = Column(Text)
    temperature = Column(Float)
    top_p = Column(Float)
    presence_penalty = Column(Float)
    frequency_penalty = Column(Float)

    created_at = Column(DateTime, server_default=func.now())


class AgentCollaborator(Base):
    """AI 合作者（创建者可添加其他用户共同管理此 AI）"""
    __tablename__ = "agent_collaborators"

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_id = Column(Integer, ForeignKey("agents.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    can_edit = Column(Boolean, default=True)
    can_delete = Column(Boolean, default=False)
    can_manage_collaborators = Column(Boolean, default=False)
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("agent_id", "user_id", name="uq_agent_collaborator"),
    )


class CapabilityVersion(Base):
    """能力源版本 — skills/tools 版本化（平台 + 世界统一）

    能力源：platform（内置工具） / world-{id}（世界 skills 生成的工具定义）。
    每个源一条版本链：content_hash 变化 → 新版本 + changelog + definitions 快照。
    旧版本永远保留：compact 前 AI 继续用 effective 版本的旧定义（前缀缓存稳定），
    compact 后切到最新。
    """
    __tablename__ = "capability_versions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(String(50), nullable=False, comment="能力源：platform / world-{id}")
    version = Column(Integer, nullable=False, comment="版本号（每源内递增）")
    content_hash = Column(String(64), nullable=False, comment="源内容哈希（检测变更）")
    changelog = Column(Text, default="", comment="本版本变更摘要（增量注入用）")
    definitions = Column(json_column(), nullable=True, comment="工具定义快照（platform=内置全部；world=skills 转出）")
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("source", "version", name="uq_capability_ver_source_version"),
    )

class AgentHistoryEntry(Base):
    """会话历史账本条目（2026-09-25 设计，见 docs/dev/conversation_history.md）

    为什么单独一张表、而不是每轮现拼：
    - 会话上下文 = 模型看过的**账本**；段内只追加、只在 compact / 超时压缩（解锁点）重写
      → 每轮重拼字节一致 → 前缀缓存命中；
    - content 存**渲染好的最终字节**（渲染即落库）：不存半成品，否则每轮重渲染会让字节漂，
      历史自己就成了缓存杀手。

    owner 只有 agent：世界 AI 侧复用 world_chat_messages 加列，不共用这张表（服务同一套）。
    """
    __tablename__ = "agent_history_entries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_id = Column(Integer, ForeignKey("agents.id", ondelete="CASCADE"), nullable=False)
    context_ref = Column(String(64), nullable=False, comment="会话标识：group:{id} / dm:{session}")
    seq = Column(Integer, nullable=False, comment="同一会话内的单调序号，唯一的排序依据")
    kind = Column(String(16), nullable=False, comment="message/gap/tool/note/notice/suggestion/summary/thinking")
    actor = Column(String(16), nullable=False, default="system", comment="self=我 / user=用户 / world=外界 / system=平台")
    content = Column(Text, nullable=False, comment="渲染好的最终字节（渲染即落库）")
    ref = Column(String(128), nullable=True, comment="来源锚点：message_id / tool_call_id（水位推导与排查用）")
    flags = Column(json_column(), default=dict, comment="可压/已撤下等标记（只由解锁点改写）")
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("agent_id", "context_ref", "seq", name="uq_agent_history_seq"),
        Index("ix_agent_history_ctx_seq", "agent_id", "context_ref", "seq"),
    )
