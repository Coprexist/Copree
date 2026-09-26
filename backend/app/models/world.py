"""
群视界模型 — 世界（World）实体

世界 = 网页文件 + 数据文件 + 后端代码 + 世界状态的独立实体。
群聊/私信只是世界的访问入口（world_bindings）。
"""
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, Float, Boolean, ForeignKey, UniqueConstraint, func, Index, text,
)
from app.config import settings
from app.db_providers import vector_column, json_column
from app.database import Base


class World(Base):
    """世界实体

    群视界机器人 = 世界配置（creator_config），不是 agent：无账号、无好友关系。
    身份固定为 world-{id}，随世界存在而存在。
    """
    __tablename__ = "worlds"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, comment="世界名")
    description = Column(Text, default="", comment="世界观简介")
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, comment="创建者")

    status = Column(String(20), default="sleeping", comment="active | sleeping（懒加载）")
    time_flow_rate = Column(Float, default=1.0, comment="时间流速")
    world_time = Column(DateTime, nullable=True, comment="当前世界时间（懒计算后写入）")
    last_active_at = Column(DateTime, nullable=True, comment="上次活跃时刻（离线补偿基准）")

    config = Column(json_column(), default=dict, comment="sleep_memory_mb / cpu_quota / runtime_memory_mb 等")

    # 群视界机器人（世界 AI）：就是世界的配置，不是 agent、无账号
    creator_config = Column(json_column(), default=dict, comment="群视界机器人配置 {name, system_prompt, model, temperature, top_p, tools}")
    creator_notices = Column(json_column(), default=list, comment="代码改动懒通知 [{file, location, summary, at}]（用户改代码→下次对话附送）")

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class WorldBinding(Base):
    """世界入口绑定 — 群聊/私信/用户 ↔ 世界"""
    __tablename__ = "world_bindings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    world_id = Column(Integer, ForeignKey("worlds.id", ondelete="CASCADE"), nullable=False)
    entity_type = Column(String(20), nullable=False, comment="group | dm | user")
    entity_id = Column(Integer, nullable=False, comment="群 ID / 会话用户 ID / 用户 ID")
    group_type_slug = Column(String(50), nullable=True,
                             comment="群绑定的类型 slug（定义在 group_types.json，随世界打包）")

    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("world_id", "entity_type", "entity_id", name="uq_world_binding"),
    )


class GroupAssistant(Base):
    """群助手 — 独立实体（不是 agents 表行）

    与群视界 AI 同形态：无账号、无好友、不入群成员表、不占 agent 体系；
    绑定群视界群时按群类型模板自动创建，每群每助手一行。
    身份 = group-assistant-{id}，随绑定存在而存在。
    """
    __tablename__ = "group_assistants"

    id = Column(Integer, primary_key=True, autoincrement=True)
    group_id = Column(Integer, ForeignKey("groups.id", ondelete="CASCADE"), nullable=False, comment="所属群")
    world_id = Column(Integer, ForeignKey("worlds.id", ondelete="CASCADE"), nullable=False, comment="所属世界")
    group_type_slug = Column(String(50), nullable=True, comment="类型 slug（定义在 group_types.json）")
    name = Column(String(50), nullable=False, comment="助手名（如 冒险团团长，模板 default_name）")
    system_prompt = Column(Text, default="", comment="助手系统提示（模板生成）")
    model = Column(String(50), nullable=True, comment="模型（空 = 世界/默认）")
    api_key_encrypted = Column(Text, nullable=True, comment="群主填的自定义 key（Fernet 加密）")
    api_base_url = Column(Text, nullable=True, comment="自定义 API 地址")
    config = Column(json_column(), default=dict, comment="扩展配置")
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("ix_group_assistants_group", "group_id"),
        Index("ix_group_assistants_world", "world_id"),
    )


class WorldAgent(Base):
    """世界居民 AI（resident）— 现有 agent 体系里的 AI 入驻世界。

    注意：群视界机器人**不是** world_agents 行，它是 worlds.creator_config（世界配置）。
    """
    __tablename__ = "world_agents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    world_id = Column(Integer, ForeignKey("worlds.id", ondelete="CASCADE"), nullable=False)
    agent_id = Column(Integer, ForeignKey("agents.id", ondelete="CASCADE"), nullable=False)
    role = Column(String(20), default="resident", comment="resident(居民AI)；assistant(群助手)；creator 不走此表")
    group_id = Column(Integer, nullable=True, comment="群助手所属群（role=assistant 时）")
    group_type_slug = Column(String(50), nullable=True, comment="群助手所属类型 slug")

    # 代码改动懒通知：用户手动改代码后记录，下次与 creator 对话时附送
    pending_notices = Column(json_column(), default=list, comment="[{file, location, summary, at}]")

    config = Column(json_column(), default=dict, comment="角色设定 / NPC 绑定位置等")

    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("world_id", "agent_id", name="uq_world_agent"),
    )


class WorldChatMessage(Base):
    """世界 AI 对话消息（与群视界机器人的世界级会话，非 DM/agent）"""
    __tablename__ = "world_chat_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    world_id = Column(Integer, ForeignKey("worlds.id", ondelete="CASCADE"), nullable=False)
    session_id = Column(String(32), nullable=True, index=True, comment="会话 id（NULL=默认会话，/new 后新会话独立）")
    user_id = Column(Integer, nullable=True, comment="发言用户（AI 消息为 Null）")
    role = Column(String(10), nullable=False, comment="user | ai | tool | note")
    content = Column(Text, nullable=False)
    reasoning = Column(Text, nullable=True, comment="AI 思考过程（thinking 模式产生，展示用，不进上下文）")
    tool_id = Column(String(32), nullable=True, index=True, comment="工具执行 id（同 id 多状态更新，历史只留最终态；2026-08-13 新增）")
    is_error = Column(Boolean, nullable=True, default=False, comment="工具执行失败标记（刷新后保持红色展示；2026-08-13 新增）")
    tool_name = Column(String(64), nullable=True, comment="工具名（role=tool；刷新后卡片标题与详情用）")
    tool_detail = Column(Text, nullable=True, comment="工具调用详情（点开卡片展开；UI 专用，不进 LLM 上下文）")
    attachments = Column(json_column(), nullable=True, comment="消息附件 [{file_id, path, name, size, mime_type}]；图片会注入 LLM 多模态（2026-09-13 新增）")

    created_at = Column(DateTime, server_default=func.now())


class WorldAI(Base):
    """世界 AI（群视界机器人）实体 — 每个世界一个，独立表（不是 agent、无账号）

    记忆/文件/工具等共用能力都以本表为锚（如记忆按 owner_type='world_ai' + 本表 id）。
    身份 = world-{world_id}，配置不再塞 worlds.creator_config JSONB。
    """
    __tablename__ = "world_ais"

    id = Column(Integer, primary_key=True, autoincrement=True)
    world_id = Column(Integer, ForeignKey("worlds.id", ondelete="CASCADE"), nullable=False, unique=True, comment="一个世界一个世界 AI")
    name = Column(String(50), default="群视界机器人")
    system_prompt = Column(Text, default="", comment="世界 AI 系统提示词（用户可改）")
    model = Column(String(50), nullable=True, comment="None = 继承全局默认模型")
    temperature = Column(Float, default=0.8)
    top_p = Column(Float, default=0.9)
    thinking = Column(Boolean, default=False, comment="深度思考（推理 token 单独计费）")
    max_tool_rounds = Column(Integer, default=50, comment="工具循环上限（默认 50）")

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class WorldAIMemory(Base):
    """世界 AI 记忆 — 世界专属表（world_ai_memories），复用主站记忆逻辑（store/recall），工具名统一 store_memory/recall_memory"""
    __tablename__ = "world_ai_memories"

    id = Column(Integer, primary_key=True, autoincrement=True)
    world_id = Column(Integer, ForeignKey("worlds.id", ondelete="CASCADE"), nullable=False, comment="所属世界")
    title = Column(String(200), nullable=False, comment="记忆标题（简短概括）")
    content = Column(Text, nullable=False, comment="记忆详细内容")
    embedding = Column(vector_column(settings.embedding_dimension), nullable=True, comment="内容向量（检索用，维度随配置）")

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_world_ai_memories_world_id", "world_id"),
    )


class WorldStructuredRecord(Base):
    """世界 AI 结构化记忆（world_structured_records）— 对齐主站 manage_records（目录级键值）

    目录结构：{category}/{sub_key}/{field} → value；纯文本、不依赖 embedding（DeepSeek 无 embedding API）。
    UNIQUE(world_id, category, sub_key, field) 实现 upsert。
    """
    __tablename__ = "world_structured_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    world_id = Column(Integer, ForeignKey("worlds.id", ondelete="CASCADE"), nullable=False, comment="所属世界")
    category = Column(String(100), nullable=False, comment="顶层目录名（如 project / setting / knowledge）")
    sub_key = Column(String(200), nullable=False, comment="子目录名（key，如项目 id / 页面名）")
    field = Column(String(200), nullable=False, comment="字段名")
    value = Column(Text, nullable=False, comment="字段值")
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("world_id", "category", "sub_key", "field", name="uq_wsr_path"),
        Index("ix_wsr_world_category", "world_id", "category"),
    )


class WorldData(Base):
    """世界数据 — 每世界 key-value 存储（结构化/操作数据，只经 API 读写）

    代码/数据分离：
    - 代码区（发布打包）：世界根目录（网页代码/skills/main.py 等）
    - 数据区：本表（结构化数据，只经 API） + data/worlds/{id}/content/（静态文字类，自由层级，
      世界自己的产物，发布不打包，下载可选默认包含）
    """
    __tablename__ = "world_data"

    id = Column(Integer, primary_key=True, autoincrement=True)
    world_id = Column(Integer, ForeignKey("worlds.id", ondelete="CASCADE"), nullable=False, comment="所属世界")
    key = Column(String(200), nullable=False, comment="数据键（如 player.position / npc.lihua.relation）")
    value = Column(json_column(), nullable=False, server_default=text("'{}'"), comment="数据值（任意 JSON）")

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("world_id", "key", name="uq_world_data_world_key"),
    )


class WorldLLMUsage(Base):
    """世界 AI LLM 调用用量（含缓存命中）— 每世界缓存命中率统计（2.7）"""
    __tablename__ = "world_llm_usage"

    id = Column(Integer, primary_key=True, autoincrement=True)
    world_id = Column(Integer, ForeignKey("worlds.id", ondelete="CASCADE"), nullable=False, comment="所属世界")
    turn_id = Column(String(32), nullable=True, comment="轮次 id")
    round_no = Column(String(10), default="0", comment="0=首轮 / N=工具轮 / final=收尾轮")
    model = Column(String(50), nullable=True, comment="使用的模型")
    prompt_tokens = Column(Integer, default=0)
    completion_tokens = Column(Integer, default=0)
    reasoning_tokens = Column(Integer, default=0)
    cached_tokens = Column(Integer, default=0, comment="缓存命中 token（prompt_tokens_details.cached_tokens）")

    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("ix_world_llm_usage_world_id", "world_id"),
    )


class WorldMarketItem(Base):
    """世界商城商品（2026-08-07 MVP：world 世界包；block 积木后置）

    发布 = 世界代码区（不含 content/）导出 zip 存 data/market/{id}.zip + 元数据；
    导入 = 下载 zip → 创建新世界 → import_zip（一键复制）。
    """
    __tablename__ = "world_market_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    kind = Column(String(20), default="world", nullable=False, comment="world=完整世界 | block=积木组件（后置）")
    title = Column(String(100), nullable=False, comment="商品标题")
    description = Column(Text, default="", comment="商品描述")
    tags = Column(json_column(), default=list, comment="标签数组，如 [\"2d冒险\",\"卡牌\"]")
    author_id = Column(Integer, ForeignKey("users.id"), nullable=False, comment="发布者")
    author_name = Column(String(50), default="", comment="发布者名（冗余，列表免 join）")
    source_world_id = Column(Integer, nullable=True, comment="发布来源世界（kind=world 时）")
    source = Column(String(10), default="local", nullable=False, comment="local=本站发布 | github=GitHub 同步缓存")
    github_path = Column(String(255), nullable=True, comment="GitHub 仓库内目录，如 worlds/world-12")
    github_sha = Column(String(64), nullable=True, comment="GitHub 同步内容 sha（用于幂等/冲突检测）")
    package_path = Column(String(255), nullable=False, comment="zip 包相对 data/ 的路径，如 market/12.zip")
    package_size = Column(Integer, default=0, comment="zip 字节数")
    downloads = Column(Integer, default=0, comment="导入次数")
    status = Column(String(20), default="on", comment="on=在架 | off=下架")

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_world_market_items_created", "created_at"),
        Index("ix_world_market_items_kind", "kind"),
    )
