"""
系统设置 Pydantic Schema
"""
from pydantic import BaseModel, Field


class SystemSettingsResponse(BaseModel):
    """系统设置（公开）"""
    id: int
    default_language: str
    default_platform_credit: int = 0
    default_file_quota_mb: int = 100
    default_concurrent_ai_limit: int = 3
    updated_by: int | None = None
    updated_at: str | None = None
    login_providers: list[str] = ["direct"]
    require_email_verification: bool = False
    registration_enabled: bool = True
    geoip_provider_url: str | None = None
    audit_user_actions: bool = False
    audit_use_real_ip: bool = False
    audit_log_retention_days: int = 90
    daily_backup_enabled: bool = False
    daily_backup_keep: int = 7


class UpdateSystemSettingsRequest(BaseModel):
    """管理后台更新系统设置"""
    default_language: str | None = Field(None, description="全局默认语言: zh 或 en")
    default_platform_credit: int | None = Field(None, ge=0, description="全局默认平台赠送额度")
    default_file_quota_mb: int | None = Field(None, ge=1, description="新用户默认文件配额（MB），修改后所有用户同步调整")
    default_concurrent_ai_limit: int | None = Field(None, ge=1, le=20, description="新建群聊默认 AI 并发数")
    registration_enabled: bool | None = Field(None, description="是否开放注册通道")
    geoip_provider_url: str | None = Field(None, description="IP 地理位置查询后端 URL，含 {ip} 占位符")
    audit_user_actions: bool | None = Field(None, description="是否记录用户行为日志（登录、发消息等）")
    audit_use_real_ip: bool | None = Field(None, description="审计是否记录访客真实 IP（需可信代理透传 X-Real-IP）")
    audit_log_retention_days: int | None = Field(None, ge=7, le=730, description="审计日志保留天数（7-730）")
    message_retention_days: int | None = Field(None, ge=0, le=3650, description="消息保留天数（0=永久保留）")
    daily_backup_enabled: bool | None = Field(None, description="每日自动备份开关（默认关）")
    daily_backup_keep: int | None = Field(None, ge=1, le=365, description="备份保留份数，超出自动清除（1-365）")
    world_preset_suggestions: list[str] | None = Field(None, description="世界 AI 建议问题预设（你可以问按钮）")


class SetupCompleteRequest(BaseModel):
    """完成初始化设置（最终步骤调用，标记向导完成）"""
    language: str | None = Field(None, description="用户选择的语言，留空保留当前设置")
    completed: bool = Field(True, description="标记向导完成")


class LanguageUpdateRequest(BaseModel):
    """更新用户语言（向导步骤1保存，不标记完成）"""
    language: str = Field(..., description="用户选择的语言: zh 或 en")


# ── v0.2.0 邮箱认证 ──

class SmtpConfigRequest(BaseModel):
    """SMTP 配置请求"""
    host: str = Field(..., min_length=1, max_length=255)
    port: int = Field(..., ge=1, le=65535)
    username: str = Field(..., max_length=255)
    password: str | None = Field(None, description="留空保持现有密码不变")
    from_email: str = Field(..., max_length=255)
    from_name: str = Field("Copree", max_length=100)
    use_tls: bool = True


class AuthSettingsRequest(BaseModel):
    """认证设置请求"""
    require_email_verification: bool | None = Field(None)
    login_providers: list[str] | None = Field(None, min_length=1)


class AuthSettingsResponse(BaseModel):
    """认证设置完整响应（管理面板用）"""
    require_email_verification: bool = False
    login_providers: list[str] = ["direct"]
    smtp_configured: bool = False
    smtp_config: dict | None = None  # 密码已脱敏
    smtp_configs: list[dict] = []    # v0.2.0 多 SMTP（密码已脱敏）


# ── v0.2.0 多 SMTP 容灾 ──

class SmtpConfigItem(BaseModel):
    """单个 SMTP 配置项"""
    host: str = Field(..., min_length=1, max_length=255)
    port: int = Field(587, ge=1, le=65535)
    username: str = Field("", max_length=255)
    password: str | None = Field(None, description="留空保持现网不变")
    from_email: str = Field(..., max_length=255)
    from_name: str = Field("Copree", max_length=100)
    use_tls: bool = True
    is_active: bool = True
    priority: int = Field(0, ge=0)


class SmtpConfigsRequest(BaseModel):
    """多 SMTP 配置批量保存请求"""
    configs: list[SmtpConfigItem] = Field(..., min_length=1)


class SmtpTestRequest(BaseModel):
    """测试单个 SMTP 配置"""
    host: str = Field(..., min_length=1, max_length=255)
    port: int = Field(587, ge=1, le=65535)
    username: str = Field("", max_length=255)
    password: str | None = Field(None)
    from_email: str = Field(..., max_length=255)
    from_name: str = Field("Copree", max_length=100)
    use_tls: bool = True


# ── v0.2.0 自定义邮件模板 ──

class EmailTemplateItem(BaseModel):
    """单个邮件模板（subject + body_html）"""
    subject: str
    body_html: str


class EmailTemplatesData(BaseModel):
    """邮件模板数据（按语言 + 用途组织）"""
    zh: dict[str, EmailTemplateItem] = {}  # key: register/login/rebind
    en: dict[str, EmailTemplateItem] = {}


class EmailTemplatesRequest(BaseModel):
    """邮件模板保存请求"""
    templates: EmailTemplatesData
    preset: str | None = None  # gradient / simple / custom，不传则不改变


class EmailPresetRequest(BaseModel):
    """邮件模板预设切换请求"""
    preset: str = Field(..., description="gradient | simple | custom")
    templates: EmailTemplatesData | None = None
