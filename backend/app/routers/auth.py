"""
认证路由
POST /auth/register, POST /auth/login, GET /auth/me
v0.2.0: + 邮箱验证码认证
"""
from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.database import get_db
from app.routers.deps import get_user_repo, get_system_settings_repo, get_verification_repo, get_api_key_pool_repo
from app.repositories.user_repo import UserRepository, SQLAlchemyUserRepository
from app.repositories.system_settings_repo import SystemSettingsRepository
from app.repositories.verification_repo import VerificationRepository
from app.repositories.api_key_pool_repo import ApiKeyPoolRepository
from app.schemas.auth import (
    RegisterRequest, LoginRequest, TokenResponse, UserInfoResponse,
    EmailVerificationRequest, VerifyEmailRequest, RebindEmailRequest,
    LoginProvidersResponse,
)
from app.schemas.system_settings import SetupCompleteRequest, LanguageUpdateRequest
from app.services.infrastructure.auth_service import (
    register_user, login_user, get_user_info,
    update_user_settings, rebind_email, unbind_email,
)
from app.services.infrastructure.verification_service import generate_and_send_code, verify_code
from app.utils.auth import get_current_user
from app.models.user import User

router = APIRouter(prefix="/auth", tags=["认证"])


@router.get("/has-users")
async def has_users(db: AsyncSession = Depends(get_db)):
    """检查是否已有注册用户（公开接口，注册页用；排除系统用户）"""
    count = (await db.execute(select(func.count(User.id)).where(User.type.notin_(("system", "external"))))).scalar()
    return {"has_users": count > 0}


@router.post("/register", response_model=TokenResponse)
async def register(
    req: RegisterRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user_repo: UserRepository = Depends(get_user_repo),
    settings_repo: SystemSettingsRepository = Depends(get_system_settings_repo),
    verification_repo: VerificationRepository = Depends(get_verification_repo),
):
    """注册新用户。第一个注册的用户自动成为管理员。"""
    try:
        user = await register_user(
            username=req.username,
            password=req.password,
            email=req.email,
            verification_code=req.verification_code,
            user_repo=user_repo,
            settings_repo=settings_repo,
            verification_repo=verification_repo,
        )
        from app.repositories.audit_repo import SQLAlchemyAuditRepository
        from app.services.audit_service import log_user_action
        ip = request.client.host if request.client else None
        await log_user_action(SQLAlchemyAuditRepository(db), "register", user.id, "user", details={"username": req.username}, ip=ip)
        # 注册后自动登录（使用用户名+密码方式）
        return await login_user(
            login_id=req.username,
            password=req.password,
            method="direct",
            user_repo=user_repo,
            settings_repo=settings_repo,
            verification_repo=verification_repo,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/login", response_model=TokenResponse)
async def login(
    req: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user_repo: UserRepository = Depends(get_user_repo),
    settings_repo: SystemSettingsRepository = Depends(get_system_settings_repo),
    verification_repo: VerificationRepository = Depends(get_verification_repo),
):
    """用户登录，返回 JWT 令牌。"""
    try:
        result = await login_user(
            login_id=req.login_id,
            password=req.password,
            method=req.method,
            verification_code=req.verification_code,
            user_repo=user_repo,
            settings_repo=settings_repo,
            verification_repo=verification_repo,
        )
        from app.repositories.audit_repo import SQLAlchemyAuditRepository
        from app.services.audit_service import log_user_action
        ip = request.client.host if request.client else None
        await log_user_action(SQLAlchemyAuditRepository(db), "login", result["user_id"], "user", details={"method": req.method or "password"}, ip=ip)
        return result
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))


@router.get("/me", response_model=UserInfoResponse)
async def me(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    user_repo: UserRepository = Depends(get_user_repo),
    api_key_pool_repo: ApiKeyPoolRepository = Depends(get_api_key_pool_repo),
):
    """获取当前用户信息。"""
    try:
        return await get_user_info(
            user_id=current_user["user_id"],
            user_repo=user_repo,
            api_key_pool_repo=api_key_pool_repo,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.post("/setup")
async def complete_setup(
    req: SetupCompleteRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """完成初始化设置向导（最终步骤调用，标记向导完成）"""
    result = await db.execute(select(User).where(User.id == current_user["user_id"]))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    # 保存语言（如果提供了）
    if req.language:
        if req.language not in ("zh", "en", "ja"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="不支持的语言")
        user.language = req.language
    # 标记向导完成
    if req.completed:
        user.setup_completed = True
    await db.flush()
    return {"status": "ok", "setup_completed": True}


@router.patch("/language")
async def update_language(
    req: LanguageUpdateRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """更新用户语言（向导步骤1调用，不标记完成）"""
    if req.language not in ("zh", "en", "ja"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="不支持的语言")
    result = await db.execute(select(User).where(User.id == current_user["user_id"]))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    user.language = req.language
    await db.flush()
    return {"status": "ok", "language": req.language}


# ── v0.2.0 邮箱认证 ──

@router.get("/login-providers", response_model=LoginProvidersResponse)
async def get_login_providers(db: AsyncSession = Depends(get_db)):
    """获取当前可用的登录方式（公开，无需认证）"""
    from app.services.infrastructure.system_settings_service import get_settings
    sys = await get_settings(db)
    return {"providers": sys.get("login_providers", ["direct"])}


@router.post("/send-verification-code")
async def send_verification_code(
    req: EmailVerificationRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """发送邮箱验证码。频率限制：每分钟 1 次，每小时 5 次。"""
    try:
        # 获取客户端 IP
        ip = request.client.host if request.client else None
        # 如果是注册用途，检查邮箱唯一性（但不透露是否已存在）
        if req.purpose == "register":
            existing = await db.execute(
                select(User).where(User.email == req.email)
            )
            if existing.scalar_one_or_none():
                # 邮箱已被使用，但不透露——对外返回成功
                return {"status": "sent", "message": "如果该邮箱有效，验证码已发送"}
        # 如果是登录用途，检查邮箱是否存在（不透露）
        if req.purpose == "login":
            existing = await db.execute(
                select(User).where(User.email == req.email)
            )
            if not existing.scalar_one_or_none():
                return {"status": "sent", "message": "如果该邮箱有效，验证码已发送"}

        await generate_and_send_code(
            db, req.email, req.purpose,
            ip_address=ip,
        )
        return {"status": "sent", "message": "如果该邮箱有效，验证码已发送"}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/verify-email")
async def verify_email(
    req: VerifyEmailRequest,
    db: AsyncSession = Depends(get_db),
):
    """校验邮箱验证码"""
    ok = await verify_code(db, req.email, req.code, req.purpose)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="验证码错误或已过期",
        )
    return {"status": "verified"}


@router.put("/email", response_model=UserInfoResponse)
async def rebind_email_endpoint(
    req: RebindEmailRequest,
    current_user: dict = Depends(get_current_user),
    user_repo: UserRepository = Depends(get_user_repo),
    verification_repo: VerificationRepository = Depends(get_verification_repo),
    api_key_pool_repo: ApiKeyPoolRepository = Depends(get_api_key_pool_repo),
):
    """换绑邮箱（需登录，需新邮箱验证码）"""
    try:
        return await rebind_email(
            user_id=current_user["user_id"],
            email=req.email,
            code=req.code,
            user_repo=user_repo,
            verification_repo=verification_repo,
            api_key_pool_repo=api_key_pool_repo,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.delete("/email", response_model=UserInfoResponse)
async def remove_email_endpoint(
    current_user: dict = Depends(get_current_user),
    user_repo: UserRepository = Depends(get_user_repo),
    settings_repo: SystemSettingsRepository = Depends(get_system_settings_repo),
    api_key_pool_repo: ApiKeyPoolRepository = Depends(get_api_key_pool_repo),
):
    """解绑邮箱（需登录，仅在 require_email_verification=OFF 时允许）"""
    try:
        return await unbind_email(
            user_id=current_user["user_id"],
            user_repo=user_repo,
            settings_repo=settings_repo,
            api_key_pool_repo=api_key_pool_repo,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
