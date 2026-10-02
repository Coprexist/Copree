"""
认证路由
POST /auth/register, POST /auth/login, GET /auth/me
v0.2.0: + 邮箱验证码认证
"""
import logging

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
logger = logging.getLogger(__name__)


async def _audit_login_success(db: AsyncSession, *, user_id: int, method: str | None, ip: str | None) -> None:
    """成功登录的审计。

    与失败那条同一口径：**不看 audit_user_actions 开关**——那个开关管的是"用户行为日志"
    （发消息、改配置这类流水），而登录成功/失败是安全事件；关了它，登录在审计里就两头都看不见。
    审计写不进去也不该让已经成功的登录变成 500。
    """
    from app.repositories.audit_repo import SQLAlchemyAuditRepository
    from app.services.audit_service import create_audit_log

    try:
        await create_audit_log(
            SQLAlchemyAuditRepository(db), log_type="login", operator_type="human",
            operator_id=user_id, target_type="user",
            ip_address=ip, details={"method": method or "password"},
        )
    except Exception as e:
        logger.warning("登录成功审计写入失败：%s: %s", type(e).__name__, e)


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
    """用户登录，返回 JWT 令牌。

    失败也会留痕（审计 + 失败计数），连打到一个账号或一个来源被锁：
    见 services/infrastructure/login_guard.py。锁定期间直接 429，不再验密码。
    """
    from app.services.infrastructure import login_guard

    ip = request.client.host if request.client else None
    locked = login_guard.retry_after(req.login_id, ip)
    if locked:
        await login_guard.audit_failure(login_id=req.login_id, ip=ip, repeat=True,
                                        reason="账号或来源已被锁定", locked_for=locked)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"登录失败次数过多，请 {locked} 秒后再试",
            headers={"Retry-After": str(locked)},
        )
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
    except ValueError as e:
        # 先计数再审计：这一条可能正好把账号锁上，审计里要把锁定秒数一起记下来
        locked = login_guard.count_failure(req.login_id, ip)
        await login_guard.audit_failure(login_id=req.login_id, ip=ip,
                                        reason=str(e), locked_for=locked)
        if locked:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"登录失败次数过多，请 {locked} 秒后再试",
                headers={"Retry-After": str(locked)},
            )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))

    login_guard.clear(req.login_id)
    await _audit_login_success(db, user_id=result["user_id"], method=req.method, ip=ip)
    return result


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
