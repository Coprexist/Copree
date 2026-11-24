"""
认证服务
处理用户注册、登录、信息获取
"""
import logging
from app.models.user import User
from app.utils.auth import hash_password, verify_password, create_access_token
from app.utils.crypto import decrypt_api_key
from app.repositories.user_repo import UserRepository
from app.repositories.system_settings_repo import SystemSettingsRepository
from app.repositories.verification_repo import VerificationRepository
from app.repositories.api_key_pool_repo import ApiKeyPoolRepository

logger = logging.getLogger(__name__)


async def register_user(
    *,
    username: str,
    password: str,
    email: str | None = None,
    verification_code: str | None = None,
    admin_bypass: bool = False,
    user_repo: UserRepository,
    settings_repo: SystemSettingsRepository,
    verification_repo: VerificationRepository,
) -> User:
    """
    注册新用户。
    如果是第一个用户，自动设为 admin（跳过邮箱验证）。
    如果 require_email_verification=ON：email + verification_code 必填。
    如果 require_email_verification=OFF：email 选填。
    admin_bypass=True：管理员后台创建，跳过注册通道开关和邮箱验证。
    """
    # 检查用户名是否已存在
    existing_user = await user_repo.get_by_username(username)
    if existing_user is not None:
        raise ValueError("用户名已存在")

    # 判断是否为首个用户（排除系统用户 id=0）
    user_count = await user_repo.count_non_system_users()
    is_first = user_count == 0

    # 读取系统设置
    require_verification = False
    registration_enabled = True
    try:
        sys = await settings_repo.get_settings()
        require_verification = sys.get("require_email_verification", False)
        registration_enabled = sys.get("registration_enabled", True)
    except Exception:
        pass

    # 检查注册通道：非首个用户 + 非管理员绕过 → 关闭则拦截
    if not is_first and not admin_bypass and not registration_enabled:
        raise ValueError("当前未开放注册，请联系管理员")



    email_verified = False

    # 首个用户跳过所有邮箱限制
    if not is_first and require_verification:
        # 邮箱必填 + 验证码必填
        if not email:
            raise ValueError("需要验证邮箱才能注册")
        if not verification_code:
            raise ValueError("请输入邮箱验证码")

        # 检查邮箱唯一性
        existing_email = await user_repo.get_by_email(email)
        if existing_email:
            raise ValueError("该邮箱已被其他账号使用")

        # 验证码校验
        if not await verification_repo.verify_code(email, verification_code, "register"):
            raise ValueError("验证码错误或已过期")

        email_verified = True
    elif email:
        # 非强制模式但提供了邮箱：检查唯一性，不强制验证
        existing_email = await user_repo.get_by_email(email)
        if existing_email:
            raise ValueError("该邮箱已被其他账号使用")
        # 如果提供了验证码，校验之
        if verification_code:
            if await verification_repo.verify_code(email, verification_code, "register"):
                email_verified = True

    user = User(
        username=username,
        password_hash=hash_password(password),
        role="admin" if is_first else "user",
        ai_quota=3,
        setup_completed=False,  # 新用户需完成初始化设置向导
        email=email,
        email_verified=email_verified,
    )
    # 读取全局默认设置（语言 + 平台赠送额度）
    try:
        sys = await settings_repo.get_settings()
        user.language = sys.get("default_language", "zh")
        user.platform_gifted_credit = sys.get("default_platform_credit", 0)
        user.file_quota_mb = sys.get("default_file_quota_mb", 100)
    except Exception:
        user.language = "zh"
        user.platform_gifted_credit = 0
        user.file_quota_mb = 100
    user_repo.add(user)
    await user_repo.flush()
    await user_repo.refresh(user)

    if is_first:
        logger.info(f"🎉 首个用户 '{username}' 自动成为管理员")

    return user


async def login_user(
    *,
    login_id: str,
    password: str | None = None,
    method: str = "direct",
    verification_code: str | None = None,
    user_repo: UserRepository,
    settings_repo: SystemSettingsRepository,
    verification_repo: VerificationRepository,
) -> dict:
    """
    用户登录，返回 JWT 令牌信息。
    method='direct': login_id + password（先按 username 查，再按 email 查）
    method='email_code': login_id（已验证邮箱）+ verification_code
    """
    # 检查登录方式是否可用
    try:
        sys = await settings_repo.get_settings()
        login_providers = sys.get("login_providers", ["direct"])
        if method not in login_providers:
            raise ValueError("该登录方式暂不可用")
    except ValueError as e:
        if "该登录方式" in str(e) or "登录方式" in str(e):
            raise
    except Exception:
        pass

    # 查找用户：先按 username，再按 email
    user = await user_repo.get_by_username(login_id)
    if user is None:
        user = await user_repo.get_by_email(login_id)

    if user is None:
        raise ValueError("用户名或密码错误")

    if not user.is_active:
        raise ValueError("账号已被封禁")

    if method == "email_code":
        # 邮箱验证码登录：必须有已验证的邮箱
        email_addr = getattr(user, "email", None)
        if not email_addr:
            raise ValueError("用户名或密码错误")
        if not getattr(user, "email_verified", False):
            raise ValueError("用户名或密码错误")
        if not verification_code:
            raise ValueError("请输入验证码")

        if not await verification_repo.verify_code(email_addr, verification_code, "login"):
            raise ValueError("用户名或密码错误")

    elif method == "direct":
        if not password:
            raise ValueError("请输入密码")
        if not verify_password(password, user.password_hash):
            raise ValueError("用户名或密码错误")
    else:
        raise ValueError("不支持的登录方式")

    access_token = create_access_token({
        "user_id": user.id,
        "username": user.username,
        "role": user.role,
    })

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user_id": user.id,
        "username": user.username,
        "role": user.role,
        "setup_completed": getattr(user, "setup_completed", True),
        "language": getattr(user, "language", "zh") or "zh",
        "email": getattr(user, "email", None),
        "email_verified": getattr(user, "email_verified", False),
        "ui_prefs": getattr(user, "ui_prefs", None) or {},
    }


async def get_user_info(
    *,
    user_id: int,
    user_repo: UserRepository,
    api_key_pool_repo: ApiKeyPoolRepository,
) -> dict:
    """获取用户信息"""
    user = await user_repo.get_by_id(user_id)

    if user is None:
        raise ValueError("用户不存在")

    # v0.1.5: 查询绑定的池 Key 名
    assigned_pool_key_name = None
    try:
        assigned_pool_key_name = await api_key_pool_repo.get_assigned_pool_key_name(user_id)
    except Exception:
        pass

    from app.services.agent.api_config_resolver import _decrypt_checked, _load_default_profile

    default_profile, default_provider = await _load_default_profile(
        user_repo.session, user.id, "llm")
    default_base = default_provider.base_url if default_provider is not None else None
    default_primary = (default_profile.primary_model or default_provider.default_primary
                       if default_profile is not None and default_provider is not None else None)
    default_secondary = (default_profile.secondary_model or default_provider.default_secondary
                         if default_profile is not None and default_provider is not None else None)
    raw_default_key = ((default_profile.api_key_encrypted
                        or default_provider.default_api_key_encrypted)
                       if default_profile is not None and default_provider is not None else None)
    default_key = _decrypt_checked(raw_default_key)[0]

    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "is_active": user.is_active,
        "ai_quota": user.ai_quota,
        "api_credit": user.api_credit,
        "platform_gifted_credit": getattr(user, 'platform_gifted_credit', 0) or 0,
        "total_effective": max(0, getattr(user, 'platform_gifted_credit', 0) or 0) + (user.api_credit or 0),
        "agent_bundle_credit": user.agent_bundle_credit,
        "file_quota_mb": user.file_quota_mb,
        "avatar_url": getattr(user, 'avatar_url', None),
        "bio": getattr(user, 'bio', None),
        "status_text": getattr(user, 'status_text', None),
        "status_color": getattr(user, 'status_color', None),
        # 设置页显示的是「默认配置档」，不再回显 users 上两个已不参与解析的裸列
        "api_base_url": default_base,
        "global_chat_model": default_primary,
        "global_work_model": default_secondary,
        "has_api_key": bool(default_key),
        "api_key_last4": (default_key or "")[-4:] if default_key else "",
        "auto_approve_vector_timeout": user.auto_approve_vector_timeout,
        "auto_approve_vector_default": user.auto_approve_vector_default,
        "timezone": user.timezone or "Asia/Shanghai",
        "language": user.language or "zh",
        "ui_prefs": user.ui_prefs or {},
        "setup_completed": getattr(user, "setup_completed", True),
        "created_at": str(user.created_at) if user.created_at else None,
        # 开关已删除（池固定排在用户默认之前）；字段保留一个版本给老前端
        "prefer_own_key": False,
        "assigned_pool_key_name": assigned_pool_key_name,
        "email": getattr(user, "email", None),
        "email_verified": getattr(user, "email_verified", False),
    }


async def rebind_email(
    *,
    user_id: int,
    email: str,
    code: str,
    user_repo: UserRepository,
    verification_repo: VerificationRepository,
    api_key_pool_repo: ApiKeyPoolRepository,
) -> dict:
    """换绑邮箱。需验证新邮箱的验证码。"""
    user = await user_repo.get_by_id(user_id)
    if user is None:
        raise ValueError("用户不存在")

    if not await verification_repo.verify_code(email, code, "rebind"):
        raise ValueError("验证码错误或已过期")

    existing = await user_repo.get_by_email(email)
    if existing and existing.id != user_id:
        raise ValueError("该邮箱已被其他账号使用")

    user.email = email
    user.email_verified = True
    await user_repo.flush()
    await user_repo.refresh(user)

    logger.info(f"用户 {user_id} 已换绑邮箱 → {email}")
    return await get_user_info(
        user_id=user_id,
        user_repo=user_repo,
        api_key_pool_repo=api_key_pool_repo,
    )


async def unbind_email(
    *,
    user_id: int,
    user_repo: UserRepository,
    settings_repo: SystemSettingsRepository,
    api_key_pool_repo: ApiKeyPoolRepository,
) -> dict:
    """解绑邮箱（仅在 require_email_verification=OFF 时允许）"""
    user = await user_repo.get_by_id(user_id)
    if user is None:
        raise ValueError("用户不存在")

    try:
        sys = await settings_repo.get_settings()
        if sys.get("require_email_verification", False):
            raise ValueError("当前要求邮箱验证，无法解绑邮箱")
    except Exception:
        pass

    user.email = None
    user.email_verified = False
    await user_repo.flush()
    await user_repo.refresh(user)

    logger.info(f"用户 {user_id} 已解绑邮箱")
    return await get_user_info(
        user_id=user_id,
        user_repo=user_repo,
        api_key_pool_repo=api_key_pool_repo,
    )


async def update_user_settings(
    *,
    user_id: int,
    user_repo: UserRepository,
    api_key_pool_repo: ApiKeyPoolRepository,
    username: str | None = None,
    password: str | None = None,
    api_base_url: str | None = None,
    api_key: str | None = None,
    auto_approve_vector_timeout: int | None = None,
    auto_approve_vector_default: bool | None = None,
    timezone: str | None = None,
    language: str | None = None,
    ui_prefs: dict | None = None,
    avatar_url: str | None = None,
    bio: str | None = None,
    status_text: str | None = None,
    status_color: str | None = None,
    prefer_own_key: bool | None = None,
    global_chat_model: str | None = None,
    global_work_model: str | None = None,
) -> dict:
    """更新用户设置（含用户名和密码修改）"""
    user = await user_repo.get_by_id(user_id)
    if user is None:
        raise ValueError("用户不存在")

    if username is not None:
        existing = await user_repo.get_by_username(username)
        if existing and existing.id != user_id:
            raise ValueError("用户名已被占用")
        user.username = username
    if password is not None:
        user.password_hash = hash_password(password)
    # 旧设置页的裸 Key / 全局模型：翻译成「用户默认配置档」，不写回 users 的四个列
    # （解析链已不读它们；写了只会让界面显示一个不生效的值）。
    if (api_base_url is not None or api_key is not None
            or global_chat_model is not None or global_work_model is not None):
        from app.services.agent.legacy_config_bridge import upsert_profile

        base = api_base_url if api_base_url is not None else getattr(user, "api_base_url", None)
        if base:
            await upsert_profile(
                user_repo.session, user_id=user_id, kind="llm", base_url=base,
                api_key=api_key,
                primary_model=global_chat_model,
                secondary_model=global_work_model,
                make_default=True,
            )
        elif api_key is not None:
            logger.warning(
                "用户 %s 提交了 API Key 但没有地址，无法建默认配置档（已忽略）", user_id)
    if auto_approve_vector_timeout is not None:
        user.auto_approve_vector_timeout = auto_approve_vector_timeout
    if auto_approve_vector_default is not None:
        user.auto_approve_vector_default = auto_approve_vector_default
    if timezone is not None:
        user.timezone = timezone
    if language is not None:
        user.language = language
    if ui_prefs is not None:
        # 必须赋**新 dict**：JSON 列的原地 update 不会被 SQLAlchemy 标脏，
        # 紧接着的 refresh() 会把改动整段丢掉（用户 2026-09-23：通俗模式滑钮打不开；
        # 同一处也是主题色/ui_scale 存不下来的原因）。本文件是全仓唯一写 ui_prefs 的地方。
        user.ui_prefs = {**(user.ui_prefs or {}), **ui_prefs}
    if avatar_url is not None:
        user.avatar_url = avatar_url
    if bio is not None:
        user.bio = bio
    if status_text is not None:
        user.status_text = status_text
    if status_color is not None:
        user.status_color = status_color
    # prefer_own_key 已删除（池固定排在用户默认之前）：参数保留一个版本只为不打断
    # 老前端，值不再落库。
    if prefer_own_key is not None:
        logger.info("收到已废弃的 prefer_own_key（忽略）: user=%s", user_id)

    await user_repo.flush()
    await user_repo.refresh(user)
    return await get_user_info(
        user_id=user_id,
        user_repo=user_repo,
        api_key_pool_repo=api_key_pool_repo,
    )

