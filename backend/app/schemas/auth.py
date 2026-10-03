"""
认证相关的 Pydantic 模型
"""
from pydantic import BaseModel, Field


class RegisterRequest(BaseModel):
    """注册请求"""
    username: str = Field(..., min_length=2, max_length=50, description="用户名")
    password: str = Field(..., min_length=6, max_length=100, description="密码")
    email: str | None = Field(None, max_length=255, description="邮箱（require_email_verification=ON 时必填）")
    verification_code: str | None = Field(None, min_length=6, max_length=6, description="邮箱验证码")


class LoginRequest(BaseModel):
    """登录请求（支持多种方式）"""
    login_id: str = Field(..., description="用户名或邮箱")
    password: str | None = Field(None, description="密码（method=direct 时必填）")
    method: str = Field("direct", description="登录方式: direct / email_code")
    verification_code: str | None = Field(None, min_length=6, max_length=6, description="验证码（method=email_code 时必填）")


class EmailVerificationRequest(BaseModel):
    """发送验证码请求"""
    email: str = Field(..., max_length=255, description="目标邮箱")
    purpose: str = Field("register", description="用途: register / login / rebind")


class VerifyEmailRequest(BaseModel):
    """验证邮箱请求"""
    email: str = Field(..., max_length=255)
    code: str = Field(..., min_length=6, max_length=6)
    purpose: str = Field("register")


class RebindEmailRequest(BaseModel):
    """换绑邮箱请求（需登录）"""
    email: str = Field(..., max_length=255)
    code: str = Field(..., min_length=6, max_length=6)


class TokenResponse(BaseModel):
    """JWT 令牌响应"""
    access_token: str
    token_type: str = "bearer"
    user_id: int
    username: str
    role: str
    setup_completed: bool = True
    language: str = "zh"
    email: str | None = None
    email_verified: bool = False


class UserInfoResponse(BaseModel):
    """用户信息响应"""
    id: int
    username: str
    role: str
    is_active: bool
    ai_quota: int
    api_credit: int = 0
    api_base_url: str | None = None
    # 页面上那两个「（覆盖）」输入框要显示保存过的值：少了它们，用户存过的覆盖值在界面上永远是空的
    global_chat_model: str | None = None
    global_work_model: str | None = None
    has_api_key: bool = False  # 不返回明文 key，只返回是否已设置
    api_key_last4: str = ""  # 已设置 key 的后4位，用于前端标识
    auto_approve_vector_timeout: int
    auto_approve_vector_default: bool
    timezone: str = "Asia/Shanghai"
    agent_bundle_credit: int = 0
    file_quota_mb: int = 100
    platform_gifted_credit: int = 0
    total_effective: int = 0
    avatar_url: str | None = None
    bio: str | None = None
    status_text: str | None = None
    language: str = "zh"
    ui_prefs: dict = {}
    setup_completed: bool = True
    created_at: str | None = None
    email: str | None = None
    email_verified: bool = False


class LoginProvidersResponse(BaseModel):
    """可用的登录方式"""
    providers: list[str] = ["direct"]
