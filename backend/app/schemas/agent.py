"""
AI 代理相关的 Pydantic 模型
"""
from pydantic import BaseModel, Field, field_validator
from app.utils.text import validate_status_text


class AgentCreateRequest(BaseModel):
    """创建 AI 请求"""
    name: str = Field(..., min_length=1, max_length=50)
    system_prompt: str | None = None
    temperature: float = Field(default=0.8, ge=0, le=2.0)
    top_p: float = Field(default=0.9, ge=0, le=1.0)
    presence_penalty: float = Field(default=0.5, ge=-2.0, le=2.0)
    frequency_penalty: float = Field(default=0.5, ge=-2.0, le=2.0)
    chat_model: str | None = None  # NULL = 继承全局
    work_model: str | None = None
    thinking_enabled: bool = Field(default=False)
    hide_ai_identity: bool = Field(default=False)
    config_profile: str | None = Field(default=None, description="预设档位: chat|immersive|digital_life")
    delay_reply_enabled: bool | None = Field(default=None, description="延迟回复开关，NULL=继承全局默认")
    max_tool_rounds: int = Field(default=6, ge=1, le=20, description="单次回复最大工具调用轮次")
    alarm_max_tool_rounds: int = Field(default=10, ge=1, le=30, description="闹钟/心跳最大工具调用轮次")
    force_alarm_on_end: bool = Field(default=False, description="对话结束时强制要求 AI 设定闹钟")
    max_alarms: int = Field(default=10, ge=1, le=50, description="AI 最多可设活跃闹钟数")
    is_ai_editable: bool = Field(default=True, description="是否允许 AI 自修改配置")
    reminder_not_count: bool | None = Field(default=None, description="[已废弃] 请用 reminder_grace")
    reminder_grace: str = Field(default="every_time", description="系统提醒额外轮次: every_time|once|off")
    ai_type: str = Field(default="resonance", description="AI 类型: general|semi_general|resonance")
    api_credit_cost: int = Field(default=0, ge=0, le=100000)
    allow_friend_requests: bool = Field(default=True, description="是否允许接收好友申请")
    auto_respond_friend_request: bool = Field(default=False, description="收到好友申请时是否自动触发 API 响应")
    discoverable: bool = Field(default=True, description="是否允许他人发现与查找此AI")
    allow_others_chat: bool = Field(default=True, description="是否允许非主人触发此AI对话")
    others_chat_mode: str = Field(default="unlimited", description="允许时的模式: unlimited|quota")
    others_chat_quota: int = Field(default=30, ge=1, le=9999, description="配额上限（触发次数），仅 quota 模式生效")
    others_chat_used: int = Field(default=0, ge=0, description="当前已使用次数")
    disallow_mode: str = Field(default="strict", description="禁止时的模式: strict|own_key")
    memory_load_mode: str = Field(default="index_only", description="记忆加载模式: index_only|index_plus_recent|index_plus_semantic")
    memory_recent_count: int = Field(default=0, ge=0, le=50, description="index_plus_recent 模式下加载最近 N 个文件内容")
    memory_shared_scope: str = Field(default="private_only", description="共享记忆范围: private_only|private_plus_shared_by_user|private_plus_shared_all")
    bio: str | None = Field(default=None, max_length=500, description="AI 简介")
    status_text: str | None = Field(default=None, max_length=100, description="个性状态（中文≤10字，英文≤30字符）")
    status_color: str | None = Field(default=None, max_length=20, description="状态文字颜色(hex)")
    auto_dnd_threshold: int = Field(default=20, ge=0, le=100, description="自动免打扰触发阈值（意愿评分低于此值自动进入 DND）")
    auto_dnd_duration: int = Field(default=5, ge=1, le=1440, description="自动免打扰持续时长（分钟）")
    conversation_logs_limit: int | None = Field(default=None, description="对话日志保留上限（null=继承全局）")
    user_can_view_logs: bool | None = Field(default=None, description="用户可否查看此 AI 对话日志（null=继承全局）")
    auto_reset_quota: bool = Field(default=False, description="每次对话后自动重置配额计数")
    group_owner_pays: bool = Field(default=True, description="群聊中 AI 消息是否由群主付费")

    @field_validator("status_text")
    @classmethod
    def check_status_text(cls, v: str | None) -> str | None:
        return validate_status_text(v)


class AgentGenerateRequest(BaseModel):
    """AI 辅助生成性格请求"""
    description: str = Field(..., min_length=5, max_length=2000, description="描述你想要的 AI 性格")


class AgentGenerateResponse(BaseModel):
    """AI 辅助生成性格响应"""
    name: str
    system_prompt: str
    temperature: float
    top_p: float
    presence_penalty: float
    frequency_penalty: float


class AgentUpdateConfigRequest(BaseModel):
    """AI 自修改配置请求"""
    name: str | None = Field(default=None, min_length=1, max_length=50, description="AI 名称")
    system_prompt: str | None = None
    temperature: float | None = Field(default=None, ge=0, le=2.0)
    top_p: float | None = Field(default=None, ge=0, le=1.0)
    presence_penalty: float | None = Field(default=None, ge=-2.0, le=2.0)
    frequency_penalty: float | None = Field(default=None, ge=-2.0, le=2.0)
    chat_model: str | None = None
    work_model: str | None = None
    thinking_enabled: bool | None = None
    hide_ai_identity: bool | None = None
    emotion_vectorized: bool | None = None
    config_profile: str | None = None
    delay_reply_enabled: bool | None = None
    max_tool_rounds: int | None = Field(default=None, ge=1, le=20, description="工具调用轮次上限")
    alarm_max_tool_rounds: int | None = Field(default=None, ge=1, le=30, description="闹钟/心跳轮次上限")
    force_alarm_on_end: bool | None = None
    max_alarms: int | None = Field(default=None, ge=1, le=50, description="最大闹钟数")
    reminder_grace: str | None = Field(default=None, description="系统提醒额外轮次: every_time|once|off")
    allow_friend_requests: bool | None = Field(default=None, description="是否允许接收好友申请")
    auto_respond_friend_request: bool | None = Field(default=None, description="收到好友申请时是否自动触发 API 响应")
    avatar_url: str | None = None
    api_base_url: str | None = None
    api_key: str | None = None
    memory_load_mode: str | None = Field(default=None, description="记忆加载模式: index_only|index_plus_recent|index_plus_semantic")
    memory_recent_count: int | None = Field(default=None, ge=0, le=50, description="index_plus_recent 模式下加载最近 N 个文件内容")
    memory_shared_scope: str | None = Field(default=None, description="共享记忆范围: private_only|private_plus_shared_by_user|private_plus_shared_all")
    bio: str | None = Field(default=None, max_length=500, description="AI 简介")
    status_text: str | None = Field(default=None, max_length=100, description="个性状态（中文≤10字，英文≤30字符）")
    status_color: str | None = Field(default=None, max_length=20, description="状态文字颜色(hex)")
    allow_others_chat: bool | None = Field(default=None, description="是否允许非主人触发此AI对话")
    others_chat_mode: str | None = Field(default=None, description="允许时的模式: unlimited|quota")
    others_chat_quota: int | None = Field(default=None, ge=1, le=9999, description="配额上限（触发次数）")
    others_chat_used: int | None = Field(default=None, ge=0, description="当前已使用次数（手动设值）")
    disallow_mode: str | None = Field(default=None, description="禁止时的模式: strict|own_key")
    auto_dnd_threshold: int | None = Field(default=None, ge=0, le=100, description="自动免打扰触发阈值")
    auto_dnd_duration: int | None = Field(default=None, ge=1, le=1440, description="自动免打扰持续时长（分钟）")
    conversation_logs_limit: int | None = Field(default=None, description="对话日志保留上限（null=继承全局）")
    user_can_view_logs: bool | None = Field(default=None, description="用户可否查看此 AI 对话日志（null=继承全局）")
    auto_reset_quota: bool | None = Field(default=None, description="每次用户与 AI 对话后自动重置配额计数")
    dm_quota_config: dict | None = Field(default=None, description="AI↔AI 私信限额配置: {send:{daily,weekly,creator_chat}, receive:{...}}，0=不启用该维度")
    group_owner_pays: bool | None = Field(default=None, description="群聊中 AI 消息是否由群主付费")

    @field_validator("status_text")
    @classmethod
    def check_status_text(cls, v: str | None) -> str | None:
        return validate_status_text(v)


class AgentStateRequest(BaseModel):
    """切换状态请求"""
    target_state: str = Field(..., description="active|dnd|inactive|blocked")
    duration_hours: int | None = Field(default=None, ge=1, le=72)
    reason: str | None = None


class AgentResponse(BaseModel):
    """AI 代理响应"""
    id: int
    user_id: int | None = Field(default=None, description="AI 统一对外标识（全平台一律用 user_id）")
    owner_id: int
    name: str
    original_system_prompt: str | None
    original_temperature: float
    original_top_p: float
    original_presence_penalty: float
    original_frequency_penalty: float
    current_system_prompt: str | None
    current_temperature: float | None
    current_top_p: float | None
    current_presence_penalty: float | None
    current_frequency_penalty: float | None
    chat_model: str | None
    work_model: str | None
    state: str
    offline_until: str | None
    is_ai_editable: bool
    thinking_enabled: bool
    config_profile: str | None = None
    delay_reply_enabled: bool | None = None
    max_tool_rounds: int = 6
    alarm_max_tool_rounds: int = 10
    force_alarm_on_end: bool = False
    max_alarms: int = 10
    ai_type: str = "resonance"
    allow_friend_requests: bool = True
    auto_respond_friend_request: bool = False
    discoverable: bool = True
    allow_others_chat: bool = True
    others_chat_mode: str = "unlimited"
    others_chat_quota: int = 30
    others_chat_used: int = 0
    disallow_mode: str = "strict"
    memory_load_mode: str = "index_only"
    memory_recent_count: int = 0
    memory_shared_scope: str = "private_only"
    avatar_url: str | None = None
    bio: str | None = None
    status_text: str | None = None
    status_color: str | None = None
    auto_reset_quota: bool = False
    group_owner_pays: bool = True
    dm_quota_config: dict | None = None
    reminder_grace: str = "every_time"
    auto_dnd_threshold: int = 20
    auto_dnd_duration: int = 5
    conversation_logs_limit: int | None = None
    user_can_view_logs: bool | None = None
    created_at: str | None


class AgentConfigHistoryResponse(BaseModel):
    """配置历史响应"""
    id: int
    agent_id: int
    system_prompt: str | None
    temperature: float | None
    top_p: float | None
    presence_penalty: float | None
    frequency_penalty: float | None
    created_at: str | None


class ApplyPresetRequest(BaseModel):
    """应用配置档请求"""
    profile: str = Field(..., description="chat|immersive|digital_life")


class WorkspaceFileUpdate(BaseModel):
    """更新工作区文件"""
    file: str = Field(..., description="todo|plan|journal")
    content: str = Field(..., description="新内容")


class WorkspaceResponse(BaseModel):
    """工作区响应"""
    todo: str = ""
    plan: str = ""
    journal: str = ""
