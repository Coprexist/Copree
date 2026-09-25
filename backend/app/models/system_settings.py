"""
平台全局系统设置 — 单行表（id=1）
"""
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, func
from app.db_providers import json_column
from app.database import Base


class SystemSettings(Base):
    __tablename__ = "system_settings"

    id = Column(Integer, primary_key=True, default=1)
    default_language = Column(String(10), default="en")
    default_platform_credit = Column(Integer, default=0, comment="全局默认平台赠送额度（0=禁用）")

    federation_sync_interval_minutes = Column(Integer, default=720, comment="联邦 profile 同步间隔（分钟），默认 720（12小时）")

    orphan_retention_days = Column(Integer, default=7, comment="孤儿文件宽限期（天），到期自动物理删除")

    default_file_quota_mb = Column(Integer, default=100, comment="新用户默认文件存储配额（MB）")
    default_concurrent_ai_limit = Column(Integer, default=3, comment="新建群聊默认 AI 并发数")

    system_prompt_overrides = Column(json_column(), nullable=True, comment="系统提示词覆盖（管理员自定义 core_identity/protocols 等段）")
    system_prompt_order = Column(json_column(), nullable=True, comment="系统提示词段拼接顺序（NULL=使用代码默认 SEGMENT_ORDER）")

    # v0.2.0 邮箱认证
    smtp_config = Column(json_column(), nullable=True, comment="SMTP 邮件配置（密码 Fernet 加密）")

    # v0.2.0 自定义邮件模板
    email_templates = Column(json_column(), nullable=True, comment="自定义邮件模板（含预设选择：gradient/simple/custom）")

    # v0.2.0 LLM 厂商配置
    provider_config = Column(json_column(), nullable=True, comment="LLM 厂商预设：{provider, base_url, chat_model, work_model, embedding_model, model_options}")
    # v0.3.6 Embedding 提供方配置（DB 覆盖 env，管理员前端可视化修改）
    # 结构: {backend, base_url, api_key_encrypted, model, dimension, enabled}
    embedding_config = Column(json_column(), nullable=True, comment="Embedding 提供方配置（覆盖 EMBEDDING_* 环境变量）")
    require_email_verification = Column(Boolean, default=False, comment="注册是否必须验证邮箱（默认关闭）")
    login_providers = Column(json_column(), default=lambda: ["direct"], comment="可用登录方式: direct/email_code/wechat/qq")
    registration_enabled = Column(Boolean, default=True, comment="是否开放注册通道（默认开启）")
    geoip_provider_url = Column(String(512), nullable=True, comment="IP 地理位置查询后端 URL，含 {ip} 占位符，留空默认 http://ip-api.com/json/{ip}")
    audit_user_actions = Column(Boolean, default=False, comment="是否记录用户行为日志（登录、发消息等），默认关闭")
    audit_log_retention_days = Column(Integer, default=90, comment="审计日志保留天数，超期自动清理（默认 90 天）")
    message_retention_days = Column(Integer, default=0, comment="消息保留天数（0=永久保留）")
    world_preset_suggestions = Column(json_column(), nullable=True, comment="世界 AI 建议问题预设（「你可以问」按钮，无对话历史/兜底时展示）")
    market_config = Column(json_column(), nullable=True, comment="世界商城配置：github_repo/github_token/auto_sync_enabled")


    # 每日数据库备份（管理员开关 + 保留份数，超出自动清除）
    daily_backup_enabled = Column(Boolean, default=False, comment="每日自动备份开关（管理员控制，默认关）")
    daily_backup_keep = Column(Integer, default=7, comment="备份保留份数，超出自动清除（默认 7）")

    last_cleanup_stats = Column(json_column(), nullable=True, comment="上次清理统计：{cleaned_files, cleaned_refs, orphan_cleaned, run_at}")

    updated_by = Column(Integer, ForeignKey("users.id"))
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
