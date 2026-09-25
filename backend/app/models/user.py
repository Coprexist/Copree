"""
用户模型
"""
from sqlalchemy import Column, Integer, String, Boolean, Text, DateTime, func, BigInteger
from app.db_providers import json_column
from app.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(50), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(20), default="user")  # 'admin' | 'user'
    is_active = Column(Boolean, default=True)
    ai_quota = Column(Integer, default=3)

    # 策略模式设置
    auto_approve_vector_timeout = Column(Integer, default=60)
    auto_approve_vector_default = Column(Boolean, default=False)

    # API 配置（加密存储）
    api_base_url = Column(Text)
    api_key_encrypted = Column(Text)

    # GitHub 账户绑定（加密存储；用于商城同步时以用户身份推送）
    github_token_encrypted = Column(Text, comment="用户 GitHub token（加密）")
    github_username = Column(String(100), comment="绑定时的 GitHub 用户名")
    github_id = Column(BigInteger, comment="GitHub 数字 user id（身份锚，改名不变）")
    github_sign_key_encrypted = Column(Text, comment="Ed25519 私钥（加密存储，作者签名用）")
    github_public_key = Column(Text, comment="Ed25519 公钥（随 meta 发布供验签）")

    # 时区（IANA 格式，如 Asia/Shanghai）
    timezone = Column(String(50), default="Asia/Shanghai")

    # 用户类型：human / ai（统一 ID 空间，AI 通过 agent.user_id 关联）
    type = Column(String(10), default="human")
    origin_channel = Column(String(16), nullable=True)  # 外部通道影子账号来源：qq=QQ 通道；NULL=站内注册

    # 对话日志：用户自己保留的对话日志数（NULL=使用系统默认值，≤ 管理员上限）
    conversation_logs_limit = Column(Integer, nullable=True)

    # API 调用额度（用于 LLM API 调用计费，1 credit = 10,000 token）
    api_credit = Column(Integer, default=0)

    # 平台赠送额度（独立于兑换码额度，管理员全局调控）
    platform_gifted_credit = Column(Integer, default=0, comment="平台赠送额度（独立于兑换码额度）")

    # 优先使用本人 API Key（跳过池 Key，先用自己的）
    prefer_own_key = Column(Boolean, default=False, comment="优先使用本人API Key")

    # 用户级全局默认模型覆盖（留空=跟提供商/系统默认）
    global_chat_model = Column(String(100), nullable=True, comment="用户全局默认聊天模型覆盖")
    global_work_model = Column(String(100), nullable=True, comment="用户全局默认工作模型覆盖")

    # AI 包断额度（创建 AI 时一次性支付 api_credit_cost，该 AI 后续调用全免）
    agent_bundle_credit = Column(Integer, default=0)

    # 文件存储配额（MB）— 总配额 = 基数(default) + 加成(兑换码)
    file_quota_mb = Column(Integer, default=100)
    file_quota_bonus_mb = Column(Integer, default=0, comment="兑换码累积的额外配额")

    # 语言偏好（zh / en）
    language = Column(String(10), default="zh")

    # 界面偏好（JSONB：chat_style, mobile_layout 等）
    ui_prefs = Column(json_column(), default=dict)

    # 个人资料
    avatar_url = Column(Text, nullable=True)
    bio = Column(Text, nullable=True)

    # 自定义状态文本（展示在资料卡中，最多 100 字）
    status_text = Column(String(100), nullable=True, comment="自定义状态文本")
    status_color = Column(String(20), nullable=True, comment="状态文字颜色(hex)")

    # 邮箱认证（v0.2.0）
    email = Column(String(255), nullable=True, comment="用户邮箱，全局唯一（NULL 不受唯一约束）")
    email_verified = Column(Boolean, default=False, comment="邮箱是否已验证")

    # 初始化设置向导是否完成
    setup_completed = Column(Boolean, default=False)

    created_at = Column(DateTime, server_default=func.now())
    last_active_at = Column(DateTime(timezone=True), nullable=True, comment="最近在线时间，NULL=当前在线")
