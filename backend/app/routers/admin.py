"""
管理员面板路由
所有端点都需要 admin 权限
"""
import os, json, asyncio, secrets
import logging
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, status, Query, UploadFile, File
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, update, delete
from pydantic import BaseModel, Field
from app.config import settings
from app.services.infrastructure.maintenance import maintenance
from app.database import get_db
from app.routers.deps import get_user_repo, get_system_settings_repo, get_verification_repo
from app.repositories.user_repo import UserRepository, SQLAlchemyUserRepository
from app.repositories.system_settings_repo import SystemSettingsRepository
from app.repositories.verification_repo import VerificationRepository
from app.utils.pure.formatting import mask_api_key
from app.utils.config_resolver import find_old_config
from app.models.user import User
from app.models.agent import Agent
from app.models.group import Group
from app.models.redemption import RedemptionCode
from app.routers.ws import manager as ws_manager
from app.models.system_log import SystemLog
from app.models.opencli import OpenCLIUsageLog
from app.services.content.opencli_service import (
    get_opencli_config,
    update_opencli_config,
    list_agent_whitelist,
    update_agent_whitelist,
    list_command_whitelist,
    add_command_whitelist,
    toggle_command_whitelist,
    delete_command_whitelist,
    get_usage_logs,
)
from app.utils.auth import hash_password, require_admin, get_current_user
from app.services.infrastructure.auth_service import register_user
from app.repositories.content_repo import SQLAlchemyContentRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["管理员"])


# ---------- Pydantic 模型 ----------

class BanUserRequest(BaseModel):
    reason: str | None = None
    duration_days: int | None = None


class GenerateCodeRequest(BaseModel):
    quota_amount: int = Field(..., ge=1, le=100)
    code_type: str = Field(default="ai_quota", pattern="^(ai_quota|api_credit|agent_bundle|file_quota)$")
    expires_in_days: int = Field(..., ge=1, le=365)
    note: str | None = None              # v0.1.5: 管理员备注（保密）
    max_usage: int | None = None         # v0.1.5: 单码最大用量
    is_api_pool: bool = False            # v0.1.5: 是否是 API 池额度


class UpdateUserRoleRequest(BaseModel):
    role: str = Field(..., pattern="^(admin|user)$")


class ResetPasswordRequest(BaseModel):
    new_password: str = Field(..., min_length=6, max_length=128)


class GeoIpQuery(BaseModel):
    ips: list[str]


class UpdateAgentEditableRequest(BaseModel):
    is_ai_editable: bool


async def _log_admin_action(
    db: AsyncSession,
    operator_id: int,
    log_type: str,
    target_type: str,
    target_id: int,
    details: dict | None = None,
):
    """记录管理员操作（含哈希链），自动从 contextvar 获取客户端 IP"""
    from app.repositories.audit_repo import SQLAlchemyAuditRepository
    from app.services.audit_service import create_audit_log
    from app.utils.auth import get_current_request_ip
    await create_audit_log(
        SQLAlchemyAuditRepository(db),
        log_type=log_type,
        operator_type="human",
        operator_id=operator_id,
        target_type=target_type,
        target_id=target_id,
        details=details or {},
        ip_address=get_current_request_ip(),
    )


# ---------- 系统概览 ----------

@router.get("/overview")
async def system_overview(
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """系统概览统计"""
    # 用户数只数本实例的账号：系统账号与外部身份（QQ/联邦）不是"用户"
    user_count = (await db.execute(
        select(func.count(User.id)).where(User.type.notin_(("system", "external")))
    )).scalar()
    agent_count = (await db.execute(select(func.count(Agent.id)))).scalar()
    group_count = (await db.execute(select(func.count(Group.id)))).scalar()

    return {
        "total_users": user_count,
        "total_agents": agent_count,
        "total_groups": group_count,
        "pending_vector_requests": 0,  # TODO: 实现
    }


# ---------- 用户管理 ----------

@router.get("/users")
async def list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """用户列表（分页）"""
    offset = (page - 1) * page_size
    _local = User.type.notin_(("system", "external"))
    total = (await db.execute(select(func.count(User.id)).where(_local))).scalar()
    result = await db.execute(
        select(User).where(_local).order_by(User.created_at.desc()).offset(offset).limit(page_size)
    )
    users = result.scalars().all()

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [
            {
                "id": u.id,
                "username": u.username,
                "role": u.role,
                "is_active": u.is_active,
                "ai_quota": u.ai_quota,
                "api_credit": u.api_credit,
                "agent_bundle_credit": u.agent_bundle_credit,
                "file_quota_mb": u.file_quota_mb,
                "created_at": str(u.created_at) if u.created_at else None,
            }
            for u in users
        ],
    }


@router.post("/users/{user_id}/ban")
async def ban_user(
    user_id: int,
    req: BanUserRequest,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """封禁/解封用户"""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")

    user.is_active = not user.is_active  # 切换状态

    await _log_admin_action(
        db,
        admin["user_id"],
        "ban_user" if not user.is_active else "unban_user",
        "user",
        user_id,
        {"reason": req.reason, "duration_days": req.duration_days},
    )
    await db.flush()

    return {
        "message": f"用户 {'已封禁' if not user.is_active else '已解封'}",
        "user_id": user_id,
        "is_active": user.is_active,
    }


@router.put("/users/{user_id}/quota")
async def update_user_quota(
    user_id: int,
    quota: int = Query(..., ge=0, le=1000),
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """调整用户 AI 创建额度"""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")

    old_quota = user.ai_quota
    user.ai_quota = quota

    await _log_admin_action(
        db, admin["user_id"], "update_quota", "user", user_id,
        {"old_quota": old_quota, "new_quota": quota},
    )
    await db.flush()

    return {"message": "额度已更新", "user_id": user_id, "ai_quota": quota}


@router.put("/users/{user_id}/api-credit")
async def update_user_api_credit(
    user_id: int,
    credit: int = Query(..., ge=0, le=100000),
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """调整用户 API 调用额度"""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")

    old_credit = user.api_credit
    user.api_credit = credit

    await _log_admin_action(
        db, admin["user_id"], "update_api_credit", "user", user_id,
        {"old_credit": old_credit, "new_credit": credit},
    )
    await db.flush()

    return {"message": "API 额度已更新", "user_id": user_id, "api_credit": credit}


@router.put("/users/{user_id}/role")
async def update_user_role(
    user_id: int,
    req: UpdateUserRoleRequest,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """提升/降级用户角色（admin ↔ user）"""
    if user_id == admin["user_id"]:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="不能修改自己的角色")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")

    old_role = user.role
    user.role = req.role

    await _log_admin_action(
        db, admin["user_id"], "change_role", "user", user_id,
        {"old_role": old_role, "new_role": req.role},
    )
    await db.flush()

    return {"message": f"用户角色已从 {old_role} 更新为 {req.role}", "user_id": user_id, "role": req.role}


@router.put("/users/{user_id}/reset-password")
async def reset_user_password(
    user_id: int,
    req: ResetPasswordRequest,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """管理员重置用户密码"""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")

    user.password_hash = hash_password(req.new_password)
    await _log_admin_action(
        db, admin["user_id"], "reset_password", "user", user_id, {},
    )
    await db.flush()

    return {"message": "密码已重置", "user_id": user_id}


# ── 管理后台创建用户 ──

class AdminCreateUserRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=64, description="用户名")
    password: str = Field(..., min_length=6, max_length=128, description="密码")
    email: str | None = Field(None, description="邮箱（可选）")


@router.post("/users", status_code=201)
async def admin_create_user(
    req: AdminCreateUserRequest,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    user_repo: UserRepository = Depends(get_user_repo),
    settings_repo: SystemSettingsRepository = Depends(get_system_settings_repo),
    verification_repo: VerificationRepository = Depends(get_verification_repo),
):
    """管理员手动创建用户（绕过注册通道开关和邮箱验证）"""
    try:
        user = await register_user(
            username=req.username,
            password=req.password,
            email=req.email,
            admin_bypass=True,
            user_repo=user_repo,
            settings_repo=settings_repo,
            verification_repo=verification_repo,
        )
        await _log_admin_action(
            db, admin["user_id"], "create_user", "user", user.id,
            {"username": req.username, "email": req.email or ""},
        )
        await db.flush()
        return {"message": "用户创建成功", "user_id": user.id, "username": user.username}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── CSV 批量导入用户 ──

@router.post("/users/import-csv")
async def admin_import_users_csv(
    file: UploadFile = File(...),
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    user_repo: UserRepository = Depends(get_user_repo),
    settings_repo: SystemSettingsRepository = Depends(get_system_settings_repo),
    verification_repo: VerificationRepository = Depends(get_verification_repo),
):
    """管理员通过 CSV 批量创建用户。CSV 格式：username,password[,email]"""
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="请上传 .csv 文件")

    content = await file.read()
    try:
        text = content.decode("utf-8-sig")  # 兼容 BOM
    except UnicodeDecodeError:
        text = content.decode("gbk")  # 兼容中文编码

    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if not lines:
        raise HTTPException(status_code=400, detail="CSV 文件为空")

    # 跳过表头行
    header = lines[0].lower()
    start = 1 if "username" in header or "user" in header else 0

    results: list[dict] = []
    for i, line in enumerate(lines[start:], start=start + 1):
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2:
            results.append({"row": i, "status": "skip", "reason": "至少需要 username,password"})
            continue

        username, password = parts[0], parts[1]
        email = parts[2] if len(parts) > 2 else None

        try:
            user = await register_user(
                username=username,
                password=password,
                email=email,
                admin_bypass=True,
                user_repo=user_repo,
                settings_repo=settings_repo,
                verification_repo=verification_repo,
            )
            results.append({"row": i, "status": "ok", "user_id": user.id, "username": username})
        except ValueError as e:
            results.append({"row": i, "status": "error", "username": username, "reason": str(e)})

    created = sum(1 for r in results if r["status"] == "ok")
    failed = sum(1 for r in results if r["status"] == "error")

    await _log_admin_action(
        db, admin["user_id"], "import_users_csv", "system", 1,
        {"total": len(results), "created": created, "failed": failed},
    )
    await db.flush()

    return {
        "message": f"导入完成：成功 {created} 个，失败 {failed} 个",
        "total": len(results),
        "created": created,
        "failed": failed,
        "details": results,
    }


# ---------- AI 管理 ----------

@router.get("/agents")
async def list_all_agents(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str = Query("", description="按 AI 名称搜索"),
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """所有 AI 列表（支持按名称搜索）"""
    offset = (page - 1) * page_size
    where_clause = Agent.name.ilike(f"%{search}%") if search else True
    total = (await db.execute(select(func.count(Agent.id)).where(where_clause))).scalar()
    result = await db.execute(
        select(Agent).where(where_clause).order_by(Agent.created_at.desc()).offset(offset).limit(page_size)
    )
    agents = result.scalars().all()

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [
            {
                "id": a.id,
                "name": a.name,
                "owner_id": a.owner_id,
                "state": a.state,
                "is_ai_editable": a.is_ai_editable,
                "created_at": str(a.created_at) if a.created_at else None,
            }
            for a in agents
        ],
    }


@router.put("/agents/{agent_id}/editable")
async def toggle_ai_editable(
    agent_id: int,
    req: UpdateAgentEditableRequest,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """开关 AI 自修改能力"""
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="AI 不存在")

    agent.is_ai_editable = req.is_ai_editable

    await _log_admin_action(
        db, admin["user_id"], "toggle_ai_editable", "agent", agent_id,
        {"is_ai_editable": req.is_ai_editable},
    )
    await db.flush()

    return {
        "message": f"AI 自修改已{'开启' if req.is_ai_editable else '关闭'}",
        "agent_id": agent_id,
    }


# ---------- 群聊审查 ----------

@router.get("/groups")
async def list_all_groups(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """所有群聊列表"""
    offset = (page - 1) * page_size
    total = (await db.execute(select(func.count(Group.id)))).scalar()
    result = await db.execute(
        select(Group).order_by(Group.created_at.desc()).offset(offset).limit(page_size)
    )
    groups = result.scalars().all()

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [
            {
                "id": g.id,
                "name": g.name,
                "owner_type": g.owner_type,
                "owner_id": g.owner_id,
                "is_vector_accelerated": g.is_vector_accelerated,
                "created_at": str(g.created_at) if g.created_at else None,
            }
            for g in groups
        ],
    }


@router.delete("/groups/{group_id}")
async def disband_group(
    group_id: int,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """强制解散群聊"""
    result = await db.execute(select(Group).where(Group.id == group_id))
    group = result.scalar_one_or_none()
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="群聊不存在")

    await db.delete(group)
    await _log_admin_action(db, admin["user_id"], "disband_group", "group", group_id)
    await db.flush()

    return {"message": "群聊已解散", "group_id": group_id}


# ---------- 兑换码 ----------

@router.post("/redemption-codes")
async def generate_code(
    req: GenerateCodeRequest,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """生成兑换码（v0.1.5: 支持备注/最大用量/API 池标记）"""
    code_str = "RC-" + secrets.token_hex(8).upper()

    code = RedemptionCode(
        code=code_str,
        quota_amount=req.quota_amount,
        code_type=req.code_type,
        expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=req.expires_in_days),
        created_by=admin["user_id"],
        note=req.note,
        max_usage=req.max_usage,
        is_api_pool=req.is_api_pool,
    )
    db.add(code)

    await _log_admin_action(
        db, admin["user_id"], "generate_code", "redemption_code", 0,
        {"code": code_str, "quota_amount": req.quota_amount, "code_type": req.code_type,
         "note": req.note, "is_api_pool": req.is_api_pool},
    )
    await db.flush()

    return {
        "code": code_str,
        "quota_amount": req.quota_amount,
        "code_type": req.code_type,
        "expires_in_days": req.expires_in_days,
        "note": req.note,
        "max_usage": req.max_usage,
        "is_api_pool": req.is_api_pool,
    }


@router.get("/redemption-codes")
async def list_codes(
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """列出所有兑换码"""
    result = await db.execute(
        select(RedemptionCode).order_by(RedemptionCode.expires_at.desc())
    )
    codes = result.scalars().all()

    return [
        {
            "code": c.code,
            "quota_amount": c.quota_amount,
            "code_type": c.code_type or "ai_quota",
            "expires_at": str(c.expires_at) if c.expires_at else None,
            "used_by": c.used_by,
            "used_at": str(c.used_at) if c.used_at else None,
            "note": c.note,
            "max_usage": c.max_usage,
            "is_api_pool": c.is_api_pool if c.is_api_pool else False,
            "created_at": str(c.created_at) if c.created_at else None,
        }
        for c in codes
    ]


# ---------- API Key 池管理 ----------

class CreatePoolKeyRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    api_base_url: str | None = None
    api_key: str = Field(..., min_length=1)
    priority: int = Field(default=0, ge=0)
    concurrent_limit: int | None = Field(default=None, ge=1, description="并发上限，NULL=按模型默认")


class UpdatePoolKeyRequest(BaseModel):
    name: str | None = None
    # 重填明文（None = 不修改）：库里的密文解不开时（加密密钥换过）这是唯一的修法
    api_key: str | None = Field(default=None, min_length=1)
    api_base_url: str | None = None
    is_active: bool | None = None
    priority: int | None = None
    concurrent_limit: int | None = Field(default=None, ge=1, description="NULL=不修改，0=清除限制恢复默认")


@router.get("/api-key-pool")
async def list_pool_keys(
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """列出所有 API Key 池条目（Key 脱敏）"""
    from app.models.api_key_pool import ApiKeyPool

    result = await db.execute(select(ApiKeyPool).order_by(ApiKeyPool.priority.desc()))
    keys = result.scalars().all()

    return [
        {
            "id": k.id,
            "name": k.name,
            "api_base_url": k.api_base_url or settings.deepseek_base_url,
            "api_key_preview": mask_api_key(k.api_key_encrypted),
            "is_active": k.is_active,
            "priority": k.priority,
            "concurrent_limit": k.concurrent_limit,
            "created_at": str(k.created_at) if k.created_at else None,
            "updated_at": str(k.updated_at) if k.updated_at else None,
        }
        for k in keys
    ]


@router.post("/api-key-pool")
async def create_pool_key(
    req: CreatePoolKeyRequest,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """添加 API Key 到池中（Key 加密存储）"""
    from app.models.api_key_pool import ApiKeyPool
    from app.utils.crypto import encrypt_api_key

    encrypted = encrypt_api_key(req.api_key)

    key_entry = ApiKeyPool(
        name=req.name,
        api_base_url=req.api_base_url,
        api_key_encrypted=encrypted,
        priority=req.priority,
        concurrent_limit=req.concurrent_limit,
    )
    db.add(key_entry)

    await _log_admin_action(
        db, admin["user_id"], "create_pool_key", "api_key_pool", 0,
        {"name": req.name, "priority": req.priority},
    )
    await db.flush()

    return {
        "id": key_entry.id,
        "name": key_entry.name,
        "api_base_url": key_entry.api_base_url or settings.deepseek_base_url,
        "api_key_preview": mask_api_key(encrypted),
        "is_active": key_entry.is_active,
        "priority": key_entry.priority,
        "concurrent_limit": key_entry.concurrent_limit,
    }


@router.put("/api-key-pool/{key_id}")
async def update_pool_key(
    key_id: int,
    req: UpdatePoolKeyRequest,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """更新池 Key 配置（不能查看明文 Key）"""
    from app.models.api_key_pool import ApiKeyPool

    result = await db.execute(select(ApiKeyPool).where(ApiKeyPool.id == key_id))
    key_entry = result.scalar_one_or_none()
    if key_entry is None:
        raise HTTPException(status_code=404, detail="池 Key 不存在")

    if req.name is not None:
        key_entry.name = req.name
    if req.api_base_url is not None:
        key_entry.api_base_url = req.api_base_url
    if req.is_active is not None:
        key_entry.is_active = req.is_active
        from app.services.infrastructure.quota_service import find_best_pool_key
    if req.priority is not None:
        key_entry.priority = req.priority
    if req.concurrent_limit is not None:
        key_entry.concurrent_limit = req.concurrent_limit if req.concurrent_limit > 0 else None
    if req.api_key:
        from app.utils.crypto import encrypt_api_key
        key_entry.api_key_encrypted = encrypt_api_key(req.api_key)

    await _log_admin_action(
        db, admin["user_id"], "update_pool_key", "api_key_pool", key_id,
        {"changes": req.model_dump(exclude_none=True)},
    )
    await db.flush()

    return {"message": "更新成功", "id": key_id}


@router.post("/api-key-pool/{key_id}/test")
async def test_pool_key(
    key_id: int,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """测通池 Key：解密 → 探测 provider（策略/文案/脱敏复用 api_probe，这里只做取 key + 转结构）。"""
    from app.models.api_key_pool import ApiKeyPool
    from app.services.agent.api_probe import probe_provider
    from app.utils.crypto import APIKeyDecryptError, decrypt_api_key

    row = (await db.execute(select(ApiKeyPool).where(ApiKeyPool.id == key_id))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="池 Key 不存在")
    try:
        key = decrypt_api_key(row.api_key_encrypted)
    except APIKeyDecryptError as e:
        return {"ok": False, "code": "decrypt_failed", "message": f"解密失败：{e}", "models": []}
    probe = await probe_provider(row.api_base_url or settings.deepseek_base_url, key, allow_private=True)
    return {
        "ok": probe.ok, "code": probe.kind, "message": probe.message,
        "models": list(probe.models or []),
    }


@router.delete("/api-key-pool/{key_id}")
async def delete_pool_key(
    key_id: int,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """删除池 Key（同时清除已有用户绑定）"""
    from app.models.api_key_pool import ApiKeyPool, UserApiAssignment

    result = await db.execute(select(ApiKeyPool).where(ApiKeyPool.id == key_id))
    key_entry = result.scalar_one_or_none()
    if key_entry is None:
        raise HTTPException(status_code=404, detail="池 Key 不存在")

    # 清除绑定此 Key 的用户
    bindings = await db.execute(
        select(UserApiAssignment).where(UserApiAssignment.pool_key_id == key_id)
    )
    for b in bindings.scalars().all():
        await db.delete(b)

    await db.delete(key_entry)

    await _log_admin_action(
        db, admin["user_id"], "delete_pool_key", "api_key_pool", key_id,
        {"name": key_entry.name},
    )
    await db.flush()

    return {"message": f"池 Key「{key_entry.name}」已删除"}




# ---------- API Key 池统计 ----------

@router.get("/api-key-pool/stats/summary")
async def get_pool_key_stats_summary(
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """获取所有池 Key 的汇总统计（含当前并发）"""
    from app.models.api_key_pool import ApiKeyPool
    from app.models.api_usage_log import ApiUsageLog
    from app.services.infrastructure.api_key_concurrency import concurrency_mgr
    from sqlalchemy import func as sqlfunc

    result = await db.execute(select(ApiKeyPool).order_by(ApiKeyPool.priority.desc()))
    keys = result.scalars().all()

    concurrency_stats = concurrency_mgr.get_stats()
    summary = []
    for k in keys:
        usage_result = await db.execute(
            select(
                sqlfunc.count(ApiUsageLog.id).label("total_requests"),
                sqlfunc.coalesce(sqlfunc.sum(ApiUsageLog.tokens_used), 0).label("total_tokens"),
                sqlfunc.coalesce(sqlfunc.sum(ApiUsageLog.credit_spent), 0).label("total_credit"),
            ).where(ApiUsageLog.pool_key_id == k.id)
        )
        row = usage_result.one()
        summary.append({
            "id": k.id,
            "name": k.name,
            "is_active": k.is_active,
            "priority": k.priority,
            "concurrent_limit": k.concurrent_limit,
            "current_concurrency": concurrency_stats["concurrency"].get(k.id, 0),
            "total_requests": row.total_requests,
            "total_tokens": int(row.total_tokens),
            "total_credit": float(row.total_credit),
        })
    return summary


@router.get("/api-key-pool/{key_id}/stats")
async def get_pool_key_stats(
    key_id: int,
    days: int = 30,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """获取单个池 Key 的详细使用统计"""
    from app.models.api_key_pool import ApiKeyPool
    from app.models.api_usage_log import ApiUsageLog
    from sqlalchemy import func as sqlfunc
    from datetime import datetime, timezone, timedelta

    result = await db.execute(select(ApiKeyPool).where(ApiKeyPool.id == key_id))
    key_entry = result.scalar_one_or_none()
    if key_entry is None:
        raise HTTPException(status_code=404, detail="池 Key 不存在")

    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)

    overview_result = await db.execute(
        select(
            sqlfunc.count(ApiUsageLog.id).label("total_requests"),
            sqlfunc.coalesce(sqlfunc.sum(ApiUsageLog.tokens_used), 0).label("total_tokens"),
            sqlfunc.coalesce(sqlfunc.sum(ApiUsageLog.credit_spent), 0).label("total_credit"),
            sqlfunc.count(sqlfunc.distinct(ApiUsageLog.user_id)).label("active_users"),
        ).where(
            ApiUsageLog.pool_key_id == key_id,
            ApiUsageLog.created_at >= since,
        )
    )
    overview = overview_result.one()

    daily_result = await db.execute(
        select(
            sqlfunc.date(ApiUsageLog.created_at).label("day"),
            sqlfunc.coalesce(sqlfunc.sum(ApiUsageLog.tokens_used), 0).label("tokens"),
            sqlfunc.count(ApiUsageLog.id).label("requests"),
        ).where(
            ApiUsageLog.pool_key_id == key_id,
            ApiUsageLog.created_at >= since,
        ).group_by(sqlfunc.date(ApiUsageLog.created_at)).order_by("day")
    )
    daily = [
        {"day": str(row.day), "tokens": int(row.tokens), "requests": row.requests}
        for row in daily_result.all()
    ]

    model_result = await db.execute(
        select(
            ApiUsageLog.model,
            sqlfunc.count(ApiUsageLog.id).label("count"),
            sqlfunc.coalesce(sqlfunc.sum(ApiUsageLog.tokens_used), 0).label("tokens"),
        ).where(
            ApiUsageLog.pool_key_id == key_id,
            ApiUsageLog.created_at >= since,
            ApiUsageLog.model.isnot(None),
        ).group_by(ApiUsageLog.model)
    )
    model_dist = [
        {"model": row.model or "unknown", "count": row.count, "tokens": int(row.tokens)}
        for row in model_result.all()
    ]

    return {
        "key_id": key_id,
        "key_name": key_entry.name,
        "overview": {
            "total_requests": overview.total_requests,
            "total_tokens": int(overview.total_tokens),
            "total_credit": float(overview.total_credit),
            "active_users": overview.active_users,
            "days": days,
        },
        "daily": daily,
        "model_distribution": model_dist,
    }


# ---------- 系统日志 ----------

@router.get("/logs")
async def system_logs(
    log_type: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """查看系统日志"""
    offset = (page - 1) * page_size
    query = select(SystemLog)
    if log_type:
        query = query.where(SystemLog.log_type == log_type)
    query = query.order_by(SystemLog.created_at.desc()).offset(offset).limit(page_size)

    total_query = select(func.count(SystemLog.id))
    if log_type:
        total_query = total_query.where(SystemLog.log_type == log_type)

    total = (await db.execute(total_query)).scalar()
    result = await db.execute(query)
    logs = result.scalars().all()





    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [log.to_dict() for log in logs],
    }


@router.get("/logs/export")
async def export_audit_logs(
    log_type: str | None = Query(None),
    operator_type: str | None = Query(None),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
    success: bool | None = Query(None),
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """导出审计日志为 CSV"""
    query = select(SystemLog).order_by(SystemLog.created_at.desc())
    if log_type:
        query = query.where(SystemLog.log_type == log_type)
    if operator_type:
        query = query.where(SystemLog.operator_type == operator_type)
    if start_date:
        try:
            query = query.where(SystemLog.created_at >= datetime.fromisoformat(start_date))
        except ValueError:
            pass
    if end_date:
        try:
            query = query.where(SystemLog.created_at <= datetime.fromisoformat(end_date))
        except ValueError:
            pass
    if success is not None:
        query = query.where(SystemLog.success == success)

    result = await db.execute(query)
    logs = result.scalars().all()

    import csv, io
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["ID","时间","类型","操作者","操作者ID","目标","目标ID","成功","错误","IP","变更前","变更后"])
    for log in logs:
        writer.writerow([log.id, log.created_at, log.log_type, log.operator_type,
            log.operator_id, log.target_type, log.target_id, log.success,
            log.error_message or "", log.ip_address or "",
            str(log.old_value or ""), str(log.new_value or "")])

    return Response(content=buf.getvalue(), media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=audit_logs.csv"})


@router.get("/logs/verify")
async def verify_audit_chain(
    limit: int = Query(1000, ge=1, le=10000),
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """验证审计日志哈希链完整性"""
    from app.services.audit_service import verify_audit_chain
    return await verify_audit_chain(db, limit=limit)


@router.post("/logs/cleanup")
async def cleanup_old_logs(
    days: int = Query(0, ge=0, le=730),
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """清理审计日志。days=0 时从系统设置读取保留天数。"""
    if days == 0:
        from app.services.infrastructure.system_settings_service import get_settings
        s = await get_settings(db)
        days = s.get("audit_log_retention_days", 90)
    from app.services.audit_service import cleanup_old_logs
    return await cleanup_old_logs(db, days=days)


# ── 消息清理 ──


@router.post("/messages/cleanup")
async def cleanup_messages(
    days: int = Query(0, ge=0, le=3650),
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """清理超过 N 天的消息。days=0 时从系统设置读取保留天数。
    历史配置变更不影响已清理的消息。"""
    if days == 0:
        from app.services.infrastructure.system_settings_service import get_settings
        s = await get_settings(db)
        days = s.get("message_retention_days", 0)

    if days <= 0:
        return {"message": "消息保留天数设为 0（永久保留），未执行清理", "deleted": 0}

    from datetime import datetime, timedelta
    cutoff = datetime.utcnow() - timedelta(days=days)
    cutoff_str = cutoff.isoformat()

    from sqlalchemy import text
    total = 0
    for table in ("messages", "dm_messages"):
        while True:
            result = await db.execute(
                text(f"DELETE FROM {table} WHERE id IN (SELECT id FROM {table} WHERE created_at < :cutoff LIMIT 5000)"),
                {"cutoff": cutoff},
            )
            deleted = result.rowcount
            total += deleted
            if deleted < 5000:
                break
            await db.commit()

    await _log_admin_action(
        db, admin["user_id"], "cleanup_messages", "system", 1,
        {"days": days, "deleted": total},
    )
    await db.commit()

    return {"message": f"已清理 {total} 条消息", "deleted": total, "cutoff": cutoff_str}


# ── IP 地理位置 ──


@router.post("/geoip/resolve")
async def resolve_geoip(
    req: GeoIpQuery,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """批量查询 IP 地理位置"""
    from app.services.infrastructure.geoip_service import resolve
    from app.services.infrastructure.system_settings_service import get_settings

    settings = await get_settings(db)
    provider_url = settings.get("geoip_provider_url") or None

    results: dict[str, dict | None] = {}
    for ip in set(req.ips):
        results[ip] = await resolve(ip, provider_url=provider_url)
    return {"results": results}


# ============================================================
# 数据库备份/恢复（策略模式，支持 PostgreSQL / SQLite）
# ============================================================

@router.get("/backup/info")
async def backup_info(
    admin: dict = Depends(require_admin),
):
    """返回当前数据库备份后端信息（类型、文件扩展名等），供前端展示"""
    from app.services.infrastructure.backup_service import get_backup_info
    return get_backup_info()


@router.get("/backup/download")
async def download_backup(
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """下载数据库备份（.sql 或 .db，取决于当前后端）"""
    from app.services.infrastructure.backup_service import create_backup, get_backup_backend

    backend = get_backup_backend()
    try:
        db_bytes = await create_backup()
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    ext = backend.backup_extension  # ".sql" or ".db"
    media_type = "application/sql" if ext == ".sql" else "application/octet-stream"

    await _log_admin_action(
        db, admin["user_id"],
        "db_backup", "system", 0,
        {"size_bytes": len(db_bytes), "db_backend": backend.name},
    )

    return Response(
        content=db_bytes,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="copree_backup_{timestamp}{ext}"',
        },
    )


# 接受的数据库备份扩展名
_VALID_DB_EXTENSIONS = (".sql", ".db")


@router.post("/backup/restore")
async def upload_restore(
    file: UploadFile = File(...),
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """上传数据库备份文件并恢复（⚠️ 覆盖当前所有数据）"""
    from app.services.infrastructure.backup_service import restore_backup, get_backup_backend

    if not file.filename or not file.filename.endswith(_VALID_DB_EXTENSIONS):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"仅支持 {_VALID_DB_EXTENSIONS} 备份文件（取决于当前数据库类型）",
        )

    try:
        content = await file.read()
        result = await restore_backup(content)
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

    backend = get_backup_backend()
    await _log_admin_action(
        db, admin["user_id"], "db_restore", "system", 0,
        {"filename": file.filename, "size_bytes": len(content), "db_backend": backend.name},
    )

    return result


class RestoreLocalRequest(BaseModel):
    """从服务器本地备份回档"""
    filename: str = Field(..., description="备份文件名（data/backups/ 下）")


@router.get("/backups")
async def list_local_backups(
    admin: dict = Depends(require_admin),
):
    """列出服务器上的本地自动备份（data/backups/，每日备份功能产生）"""
    from app.services.infrastructure.backup_service import list_backup_files

    files = list_backup_files()
    return {"backups": [
        {
            "name": f.name,
            "size_bytes": f.stat().st_size,
            "mtime": datetime.fromtimestamp(f.stat().st_mtime, timezone.utc).isoformat(),
        }
        for f in files
    ]}


@router.post("/backup/restore-local")
async def restore_local_backup(
    req: RestoreLocalRequest,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """从服务器本地备份回档（⚠️ 覆盖当前所有数据）"""
    import gzip
    from app.services.infrastructure.backup_service import restore_backup, BACKUP_DIR

    # 防路径穿越：只允许备份目录内的 copree_*.sql.gz / copree_*.db.gz（改名前的 aischat_* 一并放行）
    name = req.filename
    if (
        not name
        or "/" in name or "\\" in name or ".." in name
        or not name.startswith(("copree_", "aischat_"))
        or not (name.endswith(".sql.gz") or name.endswith(".db.gz"))
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="非法备份文件名（仅允许 copree_*.sql.gz 或 copree_*.db.gz）",
        )

    path = BACKUP_DIR / name
    if not path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="备份文件不存在")

    try:
        with gzip.open(path, "rb") as f:
            db_bytes = f.read()
        result = await restore_backup(db_bytes)
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

    await _log_admin_action(
        db, admin["user_id"], "db_restore_local", "system", 0,
        {"filename": name, "size_bytes": len(db_bytes)},
    )
    return result


@router.get("/backup/full/download")
async def download_full_backup(
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """下载完整备份（.tar.gz = 数据库备份 + 文件目录 /app/data/）"""
    from app.services.infrastructure.backup_service import create_full_backup

    try:
        tar_bytes, db_size, file_count = await create_full_backup()
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    await _log_admin_action(
        db, admin["user_id"],
        "full_backup", "system", 0,
        {"db_size": db_size, "file_count": file_count, "total_bytes": len(tar_bytes)},
    )

    return Response(
        content=tar_bytes,
        media_type="application/gzip",
        headers={
            "Content-Disposition": f'attachment; filename="copree_full_{timestamp}.tar.gz"',
        },
    )


@router.post("/backup/full/restore")
async def upload_full_restore(
    file: UploadFile = File(...),
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """上传 .tar.gz 完整备份并恢复（⚠️ 覆盖数据库 + 所有文件）"""
    from app.services.infrastructure.backup_service import restore_full_backup

    if not file.filename or not (file.filename.endswith(".tar.gz") or file.filename.endswith(".tgz")):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="仅支持 .tar.gz / .tgz 完整备份文件",
        )

    try:
        content = await file.read()
        result = await restore_full_backup(content)
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

    await _log_admin_action(
        db, admin["user_id"], "full_restore", "system", 0,
        {"filename": file.filename, "size_bytes": len(content)},
    )

    return result


# ============================================================
# 系统设置（全局默认语言等）
# ============================================================

from app.services.infrastructure.system_settings_service import get_settings, update_settings
from app.schemas.system_settings import UpdateSystemSettingsRequest, SystemSettingsResponse


@router.get("/system-settings", response_model=SystemSettingsResponse)
async def get_system_settings(
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """获取全局系统设置"""
    return await get_settings(db)


@router.put("/system-settings", response_model=SystemSettingsResponse)
async def update_system_settings(
    req: UpdateSystemSettingsRequest,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """更新全局系统设置（含平台赠送额度，修改会影响所有用户）"""
    try:
        result = await update_settings(
                db,
                default_language=req.default_language,
                default_platform_credit=req.default_platform_credit,
                default_file_quota_mb=req.default_file_quota_mb,
                default_concurrent_ai_limit=req.default_concurrent_ai_limit,
                registration_enabled=req.registration_enabled,
                geoip_provider_url=req.geoip_provider_url,
                audit_user_actions=req.audit_user_actions,
                audit_log_retention_days=req.audit_log_retention_days,
                message_retention_days=req.message_retention_days,
                daily_backup_enabled=req.daily_backup_enabled,
                daily_backup_keep=req.daily_backup_keep,
                world_preset_suggestions=req.world_preset_suggestions,
                updated_by=admin["user_id"],
            )
        await _log_admin_action(
            db, admin["user_id"], "update_system_settings", "system", 1,
            req.model_dump(exclude_none=True),
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.exception(f"update_system_settings 失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# 上传大小限制（运行时覆盖，重启后恢复 env 默认值）
# ============================================================

from pydantic import BaseModel as PydanticBaseModel

class UploadLimitsRequest(PydanticBaseModel):
    upload_max_size_mb: int | None = None
    avatar_max_size_mb: int | None = None


@router.get("/upload-limits")
async def get_upload_limits(
    admin: dict = Depends(require_admin),
):
    """获取当前文件/头像上传大小限制"""
    from app.config import get_effective_upload_max_size_mb, get_effective_avatar_max_size_mb, settings
    return {
        "upload_max_size_mb": get_effective_upload_max_size_mb(),
        "avatar_max_size_mb": get_effective_avatar_max_size_mb(),
        "upload_max_size_mb_default": settings.upload_max_size_mb,
        "avatar_max_size_mb_default": settings.avatar_max_size_mb,
    }


@router.put("/upload-limits")
async def update_upload_limits(
    req: UploadLimitsRequest,
    admin: dict = Depends(require_admin),
):
    """更新文件/头像上传大小限制（运行时，重启后恢复默认值）"""
    from app.config import set_runtime_setting, get_effective_upload_max_size_mb, get_effective_avatar_max_size_mb
    if req.upload_max_size_mb is not None:
        if req.upload_max_size_mb < 1 or req.upload_max_size_mb > 1024:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="上传大小限制需在 1-1024 MB 之间")
        set_runtime_setting("upload_max_size_mb", req.upload_max_size_mb)
    if req.avatar_max_size_mb is not None:
        if req.avatar_max_size_mb < 1 or req.avatar_max_size_mb > 100:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="头像大小限制需在 1-100 MB 之间")
        set_runtime_setting("avatar_max_size_mb", req.avatar_max_size_mb)
    return {
        "upload_max_size_mb": get_effective_upload_max_size_mb(),
        "avatar_max_size_mb": get_effective_avatar_max_size_mb(),
    }


# ============================================================
# AI 并发数全局管理
# ============================================================

class BulkConcurrencyRequest(PydanticBaseModel):
    concurrent_ai_limit: int = 3


@router.put("/groups/concurrency")
async def bulk_set_concurrency(
    req: BulkConcurrencyRequest,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """批量修改所有群聊的 AI 并发数（含已有群）"""
    limit = max(1, min(20, req.concurrent_ai_limit))
    from sqlalchemy import text
    await db.execute(text("UPDATE groups SET concurrent_ai_limit = :limit"), {"limit": limit})
    await db.commit()
    await _log_admin_action(
        db, admin["user_id"], "bulk_set_concurrency", "system", 1,
        {"concurrent_ai_limit": limit},
    )
    return {"concurrent_ai_limit": limit, "message": f"所有群聊 AI 并发数已设为 {limit}"}


# ============================================================
# v0.2.0 邮箱认证：SMTP 配置 + 认证设置
# ============================================================

from app.schemas.system_settings import (
    SmtpConfigRequest, AuthSettingsRequest, AuthSettingsResponse,
    SmtpConfigItem, SmtpConfigsRequest, SmtpTestRequest,
    EmailTemplatesData, EmailTemplatesRequest, EmailPresetRequest,
)
from app.utils.crypto import encrypt_api_key, decrypt_api_key


@router.get("/auth-settings", response_model=AuthSettingsResponse)
async def get_auth_settings(
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """获取认证设置（SMTP 已脱敏）"""
    s = await get_settings(db)
    smtp = s.get("smtp_config")

    # 兼容新旧格式：单对象 → 统一按数组处理
    if isinstance(smtp, dict):
        smtp_list = [smtp]
    elif isinstance(smtp, list):
        smtp_list = smtp
    else:
        smtp_list = []

    smtp_configured = bool(smtp_list and any(c.get("host") for c in smtp_list if isinstance(c, dict)))

    # 脱敏：移除密码，保留 is_active/priority
    safe_configs = []
    safe_first = None
    for cfg in smtp_list:
        if not isinstance(cfg, dict):
            continue
        safe = {
            "host": cfg.get("host", ""),
            "port": cfg.get("port", 587),
            "username": cfg.get("username", ""),
            "from_email": cfg.get("from_email", ""),
            "from_name": cfg.get("from_name", "Copree"),
            "use_tls": cfg.get("use_tls", True),
            "has_password": bool(cfg.get("password_encrypted")),
            "is_active": cfg.get("is_active", True),
            "priority": cfg.get("priority", 0),
        }
        safe_configs.append(safe)
        if safe_first is None:
            safe_first = safe

    return {
        "require_email_verification": s.get("require_email_verification", False),
        "login_providers": s.get("login_providers", ["direct"]),
        "smtp_configured": smtp_configured,
        "smtp_config": safe_first,
        "smtp_configs": safe_configs,
    }


@router.put("/auth-settings")
async def update_auth_settings(
    req: AuthSettingsRequest,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """更新认证设置（邮箱验证开关 + 登录方式）"""
    try:
        row = await _get_or_create_settings(db)

        if req.require_email_verification is not None:
            row.require_email_verification = req.require_email_verification

        if req.login_providers is not None:
            if len(req.login_providers) < 1:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="必须至少保留一种登录方式",
                )
            valid_providers = {"direct", "email_code", "wechat", "qq"}
            for p in req.login_providers:
                if p not in valid_providers:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"无效的登录方式: {p}",
                    )
            row.login_providers = req.login_providers

        await db.flush()
        await _log_admin_action(
            db, admin["user_id"], "update_auth_settings", "system", 1,
            req.model_dump(exclude_none=True),
        )
        return await get_auth_settings(admin=admin, db=db)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.put("/smtp-config")
async def update_smtp_config(
    req: SmtpConfigRequest,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """配置 SMTP 邮件服务（已废弃 — 内部转为 smtp_configs[0] 读写，请使用 /smtp-configs）"""
    try:
        row = await _get_or_create_settings(db)

        # 归一化为数组格式，取第一个配置
        raw = row.smtp_config
        if isinstance(raw, list) and raw:
            configs = list(raw)
        elif isinstance(raw, dict) and raw.get("host"):
            configs = [dict(raw)]
        else:
            configs = []

        if configs:
            smtp = dict(configs[0])
        else:
            smtp = {}

        smtp["host"] = req.host
        smtp["port"] = req.port
        smtp["username"] = req.username
        smtp["from_email"] = req.from_email
        smtp["from_name"] = req.from_name
        smtp["use_tls"] = req.use_tls
        smtp.setdefault("is_active", True)
        smtp.setdefault("priority", 0)

        # 密码：留空 = 保留现网，否则重新加密
        if req.password and req.password.strip():
            smtp["password_encrypted"] = encrypt_api_key(req.password)
        elif not smtp.get("password_encrypted") and req.password and req.password.strip():
            smtp["password_encrypted"] = encrypt_api_key(req.password)

        if configs:
            configs[0] = smtp
        else:
            configs = [smtp]
        row.smtp_config = configs
        await db.flush()
        await _log_admin_action(
            db, admin["user_id"], "update_smtp_config", "system", 1,
            {"host": req.host, "port": req.port, "username": req.username,
             "from_email": req.from_email, "from_name": req.from_name},
        )
        return await get_auth_settings(admin=admin, db=db)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/smtp-test")
async def test_smtp(
    req: SmtpConfigRequest,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """测试 SMTP 连接（不保存配置）"""
    # 如果密码留空，尝试从现有配置读取
    password = req.password or ""
    if not password.strip():
        s = await get_settings(db)
        raw = s.get("smtp_config", {}) or {}
        # 兼容数组格式
        if isinstance(raw, list) and raw:
            raw = raw[0]
        if isinstance(raw, dict) and raw.get("password_encrypted"):
            try:
                password = decrypt_api_key(raw["password_encrypted"])
            except Exception:
                pass

    config = {
        "host": req.host,
        "port": req.port,
        "username": req.username,
        "password": password,
        "from_email": req.from_email,
        "from_name": req.from_name,
        "use_tls": req.use_tls,
    }

    from app.services.infrastructure.email_service import test_smtp_connection
    ok, msg = await test_smtp_connection(config)
    return {"success": ok, "message": msg}


# ── v0.2.0 多 SMTP 配置管理 ──

@router.get("/smtp-configs")
async def get_smtp_configs(
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """获取全部 SMTP 配置列表（密码脱敏）"""
    s = await get_settings(db)
    raw = s.get("smtp_config")

    if isinstance(raw, dict):
        configs = [raw]
    elif isinstance(raw, list):
        configs = raw
    else:
        configs = []

    result = []
    for cfg in configs:
        if not isinstance(cfg, dict):
            continue
        result.append({
            "host": cfg.get("host", ""),
            "port": cfg.get("port", 587),
            "username": cfg.get("username", ""),
            "from_email": cfg.get("from_email", ""),
            "from_name": cfg.get("from_name", "Copree"),
            "use_tls": cfg.get("use_tls", True),
            "is_active": cfg.get("is_active", True),
            "priority": cfg.get("priority", 0),
            "has_password": bool(cfg.get("password_encrypted")),
        })
    return {"configs": result}


@router.put("/smtp-configs")
async def update_smtp_configs(
    req: SmtpConfigsRequest,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """批量保存 SMTP 配置（整体替换）"""
    try:
        row = await _get_or_create_settings(db)

        new_configs = []
        for item in req.configs:
            cfg = {
                "host": item.host,
                "port": item.port,
                "username": item.username,
                "from_email": item.from_email,
                "from_name": item.from_name,
                "use_tls": item.use_tls,
                "is_active": item.is_active,
                "priority": item.priority,
            }
            # 密码：提供了则加密，留空则保留现网
            if item.password and item.password.strip():
                cfg["password_encrypted"] = encrypt_api_key(item.password)
            else:
                # 尝试从旧配置中保留密码
                old = find_old_config(row.smtp_config, item.host, item.username)
                if old and old.get("password_encrypted"):
                    cfg["password_encrypted"] = old["password_encrypted"]

            new_configs.append(cfg)

        row.smtp_config = new_configs
        await db.flush()
        await _log_admin_action(
            db, admin["user_id"], "update_smtp_configs", "system", 1,
            {"count": len(new_configs)},
        )
        return await get_smtp_configs(admin=admin, db=db)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/smtp-configs/test/{index}")
async def test_smtp_by_index(
    index: int,
    password_override: SmtpTestRequest | None = None,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """测试指定索引的 SMTP 配置（可选覆盖密码，不存库）"""
    s = await get_settings(db)
    raw = s.get("smtp_config")

    if isinstance(raw, dict):
        configs = [raw]
    elif isinstance(raw, list):
        configs = raw
    else:
        raise HTTPException(status_code=404, detail="无 SMTP 配置")

    if index < 0 or index >= len(configs):
        raise HTTPException(status_code=404, detail=f"配置索引 {index} 不存在（共 {len(configs)} 个）")

    cfg = dict(configs[index])
    if not isinstance(cfg, dict) or not cfg.get("host"):
        raise HTTPException(status_code=404, detail=f"配置 #{index} 无效")

    # 解密密码
    password = ""
    if cfg.get("password_encrypted"):
        try:
            password = decrypt_api_key(cfg["password_encrypted"])
        except Exception:
            pass

    # 请求体可覆盖密码（用于测试时临时填写）
    if password_override and password_override.password and password_override.password.strip():
        password = password_override.password

    test_config = {
        "host": cfg["host"],
        "port": cfg.get("port", 587),
        "username": cfg.get("username", ""),
        "password": password,
        "from_email": cfg.get("from_email", ""),
        "from_name": cfg.get("from_name", "Copree"),
        "use_tls": cfg.get("use_tls", True),
    }

    from app.services.infrastructure.email_service import test_smtp_connection
    ok, msg = await test_smtp_connection(test_config)
    return {"success": ok, "message": msg, "index": index}


# ── v0.2.0 自定义邮件模板管理（v1.1.0: 预设选择）──

@router.get("/email-templates")
async def get_email_templates_endpoint(
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """获取当前邮件模板 + 预设名"""
    from app.services.infrastructure.email_service import get_email_templates, get_email_template_preset
    templates = await get_email_templates(db)
    preset = await get_email_template_preset(db)
    return {"templates": templates, "preset": preset}


@router.put("/email-templates")
async def update_email_templates(
    req: EmailTemplatesRequest,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """保存自定义邮件模板到 DB（支持同时切换 preset）"""
    try:
        from app.services.infrastructure.email_service import set_email_template_preset
        if req.preset:
            custom = req.templates.model_dump() if req.preset == "custom" else None
            await set_email_template_preset(db, req.preset, custom_templates=custom)
            await _log_admin_action(
                db, admin["user_id"], "set_email_preset", "system", 1,
                {"preset": req.preset},
            )
        else:
            row = await _get_or_create_settings(db)
            raw = getattr(row, "email_templates", None) or {}
            if isinstance(raw, str):
                import json
                raw = json.loads(raw)
            if isinstance(raw, dict):
                raw.update(req.templates.model_dump())
            else:
                raw = req.templates.model_dump()
            row.email_templates = raw  # type: ignore
            await db.flush()
            await _log_admin_action(
                db, admin["user_id"], "update_email_templates", "system", 1,
                {"preset": raw.get("preset", "custom")},
            )
        return await get_email_templates_endpoint(admin=admin, db=db)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.put("/email-templates/preset")
async def set_email_template_preset_endpoint(
    req: EmailPresetRequest,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """切换邮件模板预设（gradient / simple / custom）"""
    try:
        from app.services.infrastructure.email_service import set_email_template_preset as _set_preset
        custom = req.templates.model_dump() if req.templates else None if req.preset == "custom" else None
        await _set_preset(db, req.preset, custom_templates=custom)
        await _log_admin_action(
            db, admin["user_id"], "set_email_preset", "system", 1,
            {"preset": req.preset},
        )
        return await get_email_templates_endpoint(admin=admin, db=db)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/email-templates/reset")
async def reset_email_templates(
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """重置邮件模板为默认值（恢复到 gradient 预设）"""
    try:
        from app.services.infrastructure.email_service import set_email_template_preset as _set_preset
        await _set_preset(db, "gradient")
        await _log_admin_action(
            db, admin["user_id"], "reset_email_templates", "system", 1, {},
        )
        return await get_email_templates_endpoint(admin=admin, db=db)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


# ════════════════════════════════════════════════════════════
# 文件清理
# ════════════════════════════════════════════════════════════


@router.post("/cleanup/files")
async def cleanup_files(
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """扫描并清理：1) 已无引用的头像文件  2) 物理文件已丢失的 metadata 记录"""

    from app.models.file import FileMetadata as FMD, FileReference as FR, FileCollaborator as FC
    from app.models.system_settings import SystemSettings as SS
    from app.services.content.file_service import _get_physical_path

    avatar_dir = settings.avatars_dir
    cleaned_files = 0
    cleaned_refs = 0
    orphan_cleaned = 0

    # 1. 清理无引用的头像文件
    if os.path.isdir(avatar_dir):
        active_avatars = set()
        for model in [User, Agent, Group]:
            r = await db.execute(select(model.avatar_url).where(model.avatar_url.isnot(None)))
            for row in r:
                url = row[0]
                if url and '/download-avatar/' in url:
                    active_avatars.add(url.rsplit('/', 1)[-1])

        for f in os.listdir(avatar_dir):
            filepath = os.path.join(avatar_dir, f)
            if not os.path.isfile(filepath):
                continue
            if f not in active_avatars:
                try:
                    os.remove(filepath)
                    cleaned_files += 1
                except OSError:
                    pass

    # 2. 清理物理文件已丢失的 file_metadata 记录
    fm_result = await db.execute(select(FMD))
    for fm in fm_result.scalars():
        if fm.path and '/uploads/avatars/' in fm.path:
            continue
        try:
            phys_path = _get_physical_path(fm.path)
            if not os.path.isfile(phys_path):
                raise FileNotFoundError
        except (FileNotFoundError, ValueError):
            await db.execute(delete(FR).where(FR.file_id == fm.id))
            await db.execute(delete(FC).where(FC.file_id == fm.id))
            await db.delete(fm)
            orphan_cleaned += 1

    await db.flush()

    stats = {
        "cleaned_files": cleaned_files,
        "cleaned_refs": cleaned_refs,
        "orphan_cleaned": orphan_cleaned,
        "run_at": datetime.now(timezone.utc).isoformat(),
    }

    settings_result = await db.execute(select(SS).where(SS.id == 1))
    settings = settings_result.scalar_one_or_none()
    if settings:
        settings.last_cleanup_stats = stats
    await db.flush()

    return stats


@router.get("/cleanup/stats")
async def get_cleanup_stats(
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """获取上次清理统计"""
    from app.models.system_settings import SystemSettings as SS
    r = await db.execute(select(SS).where(SS.id == 1))
    row = r.scalar_one_or_none()
    return row.last_cleanup_stats or {"cleaned_files": 0, "cleaned_refs": 0, "orphan_cleaned": 0, "run_at": None}


async def _get_or_create_settings(db: AsyncSession):
    """获取或创建 system_settings 行（admin 内部使用）"""
    from app.models.system_settings import SystemSettings
    result = await db.execute(select(SystemSettings).where(SystemSettings.id == 1))
    row = result.scalar_one_or_none()
    if row is None:
        row = SystemSettings(id=1, default_language="en")
        db.add(row)
        await db.flush()
        await db.refresh(row)
    return row


# ============================================================
# 系统提示词管理
# ============================================================

class SystemPromptUpdateBody(BaseModel):
    overrides: dict | None = None  # {"core_identity": "...", "protocol_chat": "...", ...}
    segment_order: list[str] | None = None  # 段拼接顺序，如 ["core_identity","personality",...]


@router.get("/system-prompt")
async def get_system_prompt(
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """获取系统提示词的当前覆盖值及默认值（供管理员查看/编辑）"""
    from app.ai.llm import (
        CORE_IDENTITY, PROTOCOL_CHAT, PROTOCOL_IMMERSIVE,
        PROTOCOL_DIGITAL_LIFE, DM_PROTOCOL, SEGMENT_ORDER,
    )
    from app.services.infrastructure.system_settings_service import get_settings

    s = await get_settings(db)
    overrides = s.get("system_prompt_overrides") or {}
    order = s.get("system_prompt_order") if s else None
    if not order or not isinstance(order, list):
        order = list(SEGMENT_ORDER)

    return {
        "segments": [
            {
                "key": "core_identity",
                "label": "核心身份（Core Identity）",
                "description": "工具调用规则、批量发送规则、深度推理指令",
                "current": overrides.get("core_identity") or CORE_IDENTITY,
                "default": CORE_IDENTITY,
                "is_overridden": "core_identity" in overrides,
            },
            {
                "key": "personality",
                "label": "人格（Personality）",
                "description": "每个 AI 的 current_system_prompt，由 AI 创建者设置，管理员不可覆盖",
                "current": "（每个 AI 独立设置）",
                "default": "（每个 AI 独立设置）",
                "is_overridden": False,
                "readonly": True,
            },
            {
                "key": "protocol_chat",
                "label": "聊天协议（Protocol: Chat）",
                "description": "聊天模式行为协议",
                "current": overrides.get("protocol_chat") or PROTOCOL_CHAT,
                "default": PROTOCOL_CHAT,
                "is_overridden": "protocol_chat" in overrides,
            },
            {
                "key": "protocol_immersive",
                "label": "沉浸协议（Protocol: Immersive）",
                "description": "沉浸模式行为协议",
                "current": overrides.get("protocol_immersive") or PROTOCOL_IMMERSIVE,
                "default": PROTOCOL_IMMERSIVE,
                "is_overridden": "protocol_immersive" in overrides,
            },
            {
                "key": "protocol_digital_life",
                "label": "数字生命协议（Protocol: Digital Life）",
                "description": "数字生命模式行为协议",
                "current": overrides.get("protocol_digital_life") or PROTOCOL_DIGITAL_LIFE,
                "default": PROTOCOL_DIGITAL_LIFE,
                "is_overridden": "protocol_digital_life" in overrides,
            },
            {
                "key": "dm_protocol",
                "label": "私信协议（DM Protocol）",
                "description": "私信对话行为协议",
                "current": overrides.get("dm_protocol") or DM_PROTOCOL,
                "default": DM_PROTOCOL,
                "is_overridden": "dm_protocol" in overrides,
            },
            {
                "key": "tools",
                "label": "工具清单（Tools）",
                "description": "由 AI 状态和深度推理模式动态生成，不可覆盖",
                "current": "（动态生成，见工具注册表）",
                "default": "（动态生成）",
                "is_overridden": False,
                "readonly": True,
            },
            {
                "key": "current_context",
                "label": "当前上下文（Current Context）",
                "description": "时间、群名、群 ID、DM 状态等动态注入",
                "current": "（每次请求动态生成）",
                "default": "（每次请求动态生成）",
                "is_overridden": False,
                "readonly": True,
            },
            {
                "key": "injected_skills",
                "label": "注入技能（Injected Skills）",
                "description": "记忆注入 + Skill 引擎注入，动态生成",
                "current": "（每次请求动态生成）",
                "default": "（每次请求动态生成）",
                "is_overridden": False,
                "readonly": True,
            },
        ],
        "segment_order": list(order),
    }


@router.put("/system-prompt")
async def update_system_prompt(
    body: SystemPromptUpdateBody,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """更新系统提示词覆盖值（可覆盖的段：core_identity, protocol_*）"""
    from app.services.infrastructure.system_settings_service import _get_or_create

    allowed_keys = {"core_identity", "protocol_chat", "protocol_immersive", "protocol_digital_life", "dm_protocol"}
    overrides = body.overrides or {}

    # 仅允许白名单中的 key
    filtered = {k: v for k, v in overrides.items() if k in allowed_keys and v}

    s = await _get_or_create(db)
    existing = (s.system_prompt_overrides or {}).copy() if s.system_prompt_overrides else {}
    existing.update(filtered)
    s.system_prompt_overrides = existing

    # 保存段顺序（如果提供）
    order_updated = False
    if body.segment_order is not None and len(body.segment_order) > 0:
        s.system_prompt_order = body.segment_order
        order_updated = True

    await db.flush()

    log_detail = {"updated_keys": list(filtered.keys())}
    if order_updated:
        log_detail["order_updated"] = True

    await _log_admin_action(
        db, admin["user_id"], "update_system_prompt", "system", 1,
        log_detail,
    )

    result = {"message": "系统提示词已更新", "overrides": existing}
    if order_updated:
        result["segment_order"] = body.segment_order
    return result


# ============================================================
# OpenCLI 权限管理
# ============================================================

# Pydantic 模型（admin 内联）
class OpenCLIConfigBody(BaseModel):
    global_enabled: bool | None = None
    default_rate_limit_per_minute: int | None = Field(default=None, ge=1, le=60)
    timeout_seconds: int | None = Field(default=None, ge=5, le=300)


class AgentWhitelistBody(BaseModel):
    enabled: bool
    rate_limit_override: int | None = None


class CommandWhitelistBody(BaseModel):
    pattern: str = Field(..., min_length=1, max_length=200)
    is_regex: bool = False
    description: str | None = Field(default=None, max_length=200)
    default_enabled: bool = False  # True=全部AI默认可用


@router.get("/opencli/config")
async def get_opencli_config_route(
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """获取 OpenCLI 全局配置"""
    return await get_opencli_config(db)


@router.put("/opencli/config")
async def update_opencli_config_route(
    req: OpenCLIConfigBody,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """更新 OpenCLI 全局配置"""
    try:
        result = await update_opencli_config(
            SQLAlchemyContentRepository(db),
            updated_by=admin["user_id"],
            global_enabled=req.global_enabled,
            default_rate_limit_per_minute=req.default_rate_limit_per_minute,
            timeout_seconds=req.timeout_seconds,
        )
        await _log_admin_action(
            db, admin["user_id"], "update_opencli_config", "opencli", 1,
            req.model_dump(exclude_none=True),
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/opencli/agents")
async def list_opencli_agents(
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """获取所有 AI 的 OpenCLI 权限状态"""
    return await list_agent_whitelist(SQLAlchemyContentRepository(db))


@router.put("/opencli/agents/{agent_id}")
async def update_opencli_agent(
    agent_id: int,
    req: AgentWhitelistBody,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """开关某 AI 的 OpenCLI 权限"""
    try:
        result = await update_agent_whitelist(
            SQLAlchemyContentRepository(db), agent_id=agent_id,
            enabled=req.enabled,
            rate_limit_override=req.rate_limit_override,
        )
        await _log_admin_action(
            db, admin["user_id"], "update_opencli_agent", "agent", agent_id,
            req.model_dump(),
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/opencli/commands")
async def list_opencli_commands(
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """获取命令白名单列表"""
    return await list_command_whitelist(SQLAlchemyContentRepository(db))


@router.post("/opencli/commands")
async def add_opencli_command(
    req: CommandWhitelistBody,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """添加命令白名单"""
    try:
        result = await add_command_whitelist(
            SQLAlchemyContentRepository(db),
            pattern=req.pattern,
            is_regex=req.is_regex,
            description=req.description,
            default_enabled=req.default_enabled,
            created_by=admin["user_id"],
        )
        await _log_admin_action(
            db, admin["user_id"], "add_opencli_command", "opencli_command", 0,
            req.model_dump(),
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.put("/opencli/commands/{cmd_id}/toggle")
async def toggle_opencli_command(
    cmd_id: int,
    enabled: bool = Query(True),
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """开关某条命令白名单"""
    try:
        return await toggle_command_whitelist(SQLAlchemyContentRepository(db), cmd_id, enabled)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.put("/opencli/commands/{cmd_id}/default")
async def toggle_opencli_command_default(
    cmd_id: int,
    default_enabled: bool = Query(True),
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """开关某条命令的「默认可用」（全部 AI 无需白名单）"""
    try:
        entry = await db.get(OpenCLICommandWhitelist, cmd_id) if False else None
        from sqlalchemy import update, text
        await db.execute(
            text("UPDATE opencli_command_whitelist SET default_enabled = :v WHERE id = :id"),
            {"v": default_enabled, "id": cmd_id},
        )
        await db.commit()
        return {"ok": True, "cmd_id": cmd_id, "default_enabled": default_enabled}
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.delete("/opencli/commands/{cmd_id}")
async def delete_opencli_command(
    cmd_id: int,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """删除命令白名单条目"""
    try:
        await delete_command_whitelist(SQLAlchemyContentRepository(db), cmd_id)
        await _log_admin_action(
            db, admin["user_id"], "delete_opencli_command", "opencli_command", cmd_id,
        )
        return {"message": "已删除", "id": cmd_id}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/opencli/commands/presets")
async def add_opencli_preset_commands(
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    一键添加预设命令白名单。
    已存在的命令会自动跳过（不重复添加），返回新增和跳过的列表。
    """
    # ⚠️ 预设命令列表：涵盖文件操作（进程内 Python 实现）、浏览器操作（opencli browser）、
    #    外部 CLI 桥接（gh/docker/obsidian 等）。管理员可根据实际需要自行增删。
    #    - is_regex=False 表示精确匹配命令名
    #    - is_regex=True  表示正则匹配（如 "gh .*" 允许所有 GitHub CLI 子命令）
    presets = [
        # ── 文件操作（AI 在自己的沙箱目录里读写，进程内 Python 实现） ──
        {"pattern": "file_read",   "is_regex": False, "description": "📖 读取文件 — 在自己文件空间里读取文本文件内容"},
        {"pattern": "file_write",  "is_regex": False, "description": "✏️ 写入文件 — 创建或覆盖自己文件空间里的文件（自动建子目录）"},
        {"pattern": "file_list",   "is_regex": False, "description": "📂 列出文件 — 浏览自己文件空间里的文件和子目录"},
        {"pattern": "file_delete", "is_regex": False, "description": "🗑️ 删除文件 — 删除自己文件空间里不需要的文件"},
        {"pattern": "file_info",   "is_regex": False, "description": "ℹ️ 文件信息 — 查看文件大小、修改时间等元信息"},
        {"pattern": "create_dir",  "is_regex": False, "description": "📁 创建目录 — 在自己文件空间里创建新文件夹"},
        # ── 浏览器自动化（操控已登录的 Chrome 浏览器） ──
        {"pattern": "browser",   "is_regex": False, "description": "🌐 浏览器操作 — AI 能打开网页、截图、点击、填表、抓取内容（依赖 Chrome 环境）"},
        {"pattern": "curl .*",  "is_regex": True,  "description": "🌐 curl 网页抓取 — 纯命令行 HTTP 请求，不需要 Chrome。用法: curl https://example.com"},
        {"pattern": "list",      "is_regex": False, "description": "📋 列出命令 — AI 查看当前可用的所有 OpenCLI 命令"},
        # ── 外部 CLI 桥接（将已有命令行工具接入 OpenCLI） ──
        {"pattern": "gh .*",     "is_regex": True,  "description": "🐙 GitHub CLI — 浏览仓库、PR、Issue、搜索（需 gh CLI 已登录）"},
        {"pattern": "docker .*", "is_regex": True,  "description": "🐳 Docker — 管理容器、镜像、查看运行状态"},
        {"pattern": "obsidian .*", "is_regex": True, "description": "📝 Obsidian — 读写笔记、搜索知识库"},
        {"pattern": "vercel .*", "is_regex": True,  "description": "▲ Vercel — 部署、查看项目、管理域名"},
        {"pattern": "tg .*",     "is_regex": True,  "description": "📨 Telegram CLI — 收发消息、管理频道"},
        {"pattern": "discord .*", "is_regex": True, "description": "💬 Discord CLI — 发消息、管理服务器"},
        {"pattern": "wx .*",     "is_regex": True,  "description": "💚 微信 CLI — 下载公众号文章、管理消息"},
    ]

    added = []
    skipped = []

    # 先获取已有的白名单，用于去重
    existing = await list_command_whitelist(SQLAlchemyContentRepository(db))
    existing_patterns = {(e["pattern"], e["is_regex"]) for e in existing}

    for p in presets:
        key = (p["pattern"], p["is_regex"])
        if key in existing_patterns:
            skipped.append(p["pattern"])
            continue
        try:
            entry = await add_command_whitelist(
                SQLAlchemyContentRepository(db),
                pattern=p["pattern"],
                is_regex=p["is_regex"],
                description=p["description"],
                created_by=admin["user_id"],
            )
            added.append(entry)
        except Exception:
            skipped.append(p["pattern"])

    await _log_admin_action(
        db, admin["user_id"], "add_opencli_presets", "opencli_command", 0,
        {"added": [a["pattern"] for a in added], "skipped": skipped},
    )

    return {
        "message": f"已添加 {len(added)} 个预设命令，跳过 {len(skipped)} 个（已存在）",
        "added": added,
        "skipped": skipped,
    }


@router.get("/opencli/logs")
async def get_opencli_logs(
    agent_id: int | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """获取 OpenCLI 使用日志"""
    return await get_usage_logs(SQLAlchemyContentRepository(db), agent_id=agent_id, page=page, page_size=page_size)


# ════════════════════════════════════════════════════════════
# 联邦通信管理（v0.1.2）
# ════════════════════════════════════════════════════════════

from app.schemas.federation import (
    InstanceConfigUpdate,
    PeerCreate,
    PeerUpdate,
    FederatedEntityUpdate,
)
from app.services.federation import federation_service as fed_svc


# ── 实例身份 ──

@router.get("/federation/instance")
async def get_federation_instance(
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """获取本实例身份信息"""
    info = await fed_svc.get_instance_info(db)
    return info


@router.put("/federation/instance")
async def update_federation_instance(
    body: InstanceConfigUpdate,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """更新本实例身份信息"""
    result = await fed_svc.update_instance_info(
        db,
        display_name=body.display_name,
        public_url=body.public_url,
        public_id=body.public_id,
    )
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["message"])
    return result


@router.post("/federation/instance/regenerate-id")
async def regenerate_federation_id(
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """重新生成公网 ID（用于冲突后的补救）"""
    result = await fed_svc.regenerate_public_id(db)
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["message"])
    return result


@router.post("/federation/instance/register")
async def register_federation_public_id(
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """将公网 ID 注册到 GitHub 注册表（带冲突检测）"""
    result = await fed_svc.register_public_id(db)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result["message"])
    return result


@router.put("/federation/instance/github-token")
async def set_federation_github_token(
    body: dict,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """在界面中配置 GitHub Token（加密存储，无需 SSH 改 .env）"""
    token = body.get("token", "")
    if not token or not token.strip():
        raise HTTPException(status_code=400, detail="Token 不能为空")
    result = await fed_svc.set_github_token(db, token.strip())
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["message"])
    return {"success": True, "message": "GitHub Token 已加密保存"}


@router.get("/federation/registry")
async def get_federation_registry(
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """拉取 GitHub 公开注册表"""
    result = await fed_svc.fetch_github_registry(db)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result["message"])
    return result


# ── 对等端管理 ──

@router.get("/federation/peers")
async def list_federation_peers(
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """列出所有对等端"""
    return await fed_svc.list_peers(db)


@router.post("/federation/peers")
async def add_federation_peer(
    body: PeerCreate,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """添加对等端"""
    result = await fed_svc.add_peer(
        db,
        peer_public_id=body.peer_public_id,
        remote_url=body.remote_url,
        shared_secret=body.shared_secret,
        display_name=body.display_name,
    )
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["message"])
    return result


@router.put("/federation/peers/{peer_id}")
async def update_federation_peer(
    peer_id: int,
    body: PeerUpdate,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """更新对等端"""
    result = await fed_svc.update_peer(
        db, peer_id,
        display_name=body.display_name,
        remote_url=body.remote_url,
        shared_secret=body.shared_secret,
        is_enabled=body.is_enabled,
    )
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["message"])
    return result


@router.delete("/federation/peers/{peer_id}")
async def delete_federation_peer(
    peer_id: int,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """移除对等端"""
    result = await fed_svc.remove_peer(db, peer_id)
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["message"])
    return result


@router.post("/federation/peers/{peer_id}/connect")
async def connect_federation_peer(
    peer_id: int,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """手动触发对等端连接"""
    from app.models.federation import FederationPeer
    result = await db.execute(select(FederationPeer).where(FederationPeer.id == peer_id))
    peer = result.scalar_one_or_none()
    if peer is None:
        raise HTTPException(status_code=404, detail="对等端不存在")

    # 空 URL → 无需出站连接（等待对方主动连入）
    if not (peer.remote_url or "").strip():
        return {"message": f"{peer.peer_public_id} 未配置远端地址，无需出站连接（等待对方连入）"}
    # 已连接 → 无需重复
    from app.services.federation.federation_manager import federation_manager
    if peer.peer_public_id in federation_manager.peers and \
       federation_manager.peers[peer.peer_public_id].handshake_complete:
        return {"message": f"{peer.peer_public_id} 已连接（入站连接）"}

    success = await federation_manager.connect_to_peer(peer)
    if not success:
        error_msg = federation_manager.get_last_error(peer.peer_public_id) or "连接失败"
        raise HTTPException(status_code=500, detail=error_msg)
    return {"message": f"已连接到 {peer.peer_public_id}"}


@router.post("/federation/peers/{peer_id}/disconnect")
async def disconnect_federation_peer(
    peer_id: int,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """手动断开对等端"""
    from app.models.federation import FederationPeer
    result = await db.execute(select(FederationPeer).where(FederationPeer.id == peer_id))
    peer = result.scalar_one_or_none()
    if peer is None:
        raise HTTPException(status_code=404, detail="对等端不存在")

    from app.services.federation.federation_manager import federation_manager
    await federation_manager.disconnect_peer(peer.peer_public_id)
    return {"message": f"已断开 {peer.peer_public_id}"}


class URLRotateRequest(BaseModel):
    new_url: str = Field(..., min_length=1, max_length=500)


@router.post("/federation/peers/{peer_id}/rotate-url")
async def rotate_federation_peer_url(
    peer_id: int,
    body: URLRotateRequest,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """手动触发对等端 URL 轮换"""
    from app.models.federation import FederationPeer
    result = await db.execute(select(FederationPeer).where(FederationPeer.id == peer_id))
    peer = result.scalar_one_or_none()
    if peer is None:
        raise HTTPException(status_code=404, detail="对等端不存在")

    if not peer.is_enabled:
        raise HTTPException(status_code=400, detail="对等端已禁用")

    from app.services.federation.federation_manager import federation_manager
    err = await federation_manager.initiate_url_rotation(peer.peer_public_id, body.new_url)
    if err:
        raise HTTPException(status_code=400, detail=err)
    return {"message": f"已发起 URL 轮换: {peer.peer_public_id} → {body.new_url}"}


# ── 联邦实体管理（v0.2.0: 替代 share_group / share_dm）──

@router.get("/federation/entities")
async def list_federated_entities(
    peer_id: int | None = None,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """列出联邦实体，可选按 peer 过滤"""
    return await fed_svc.list_federated_entities(db, peer_id=peer_id)


@router.put("/federation/entities/{entity_id}")
async def update_federated_entity(
    entity_id: int,
    body: FederatedEntityUpdate,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """更新联邦实体（启用/禁用、方向切换）"""
    result = await fed_svc.update_federated_entity(
        db, entity_id,
        is_enabled=body.is_enabled,
        direction=body.direction,
    )
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["message"])
    return result


@router.delete("/federation/entities/{entity_id}")
async def delete_federated_entity(
    entity_id: int,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """移除联邦实体"""
    result = await fed_svc.remove_federated_entity(db, entity_id)
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["message"])
    return result


# ── 对话日志管理 ──

from pydantic import BaseModel as PydanticBaseModel, Field


class ConvLogConfigBody(PydanticBaseModel):
    max_conversation_logs: int | None = Field(None, ge=1, le=500)
    default_user_conversation_logs: int | None = Field(None, ge=1, le=500)
    default_user_log_access: bool | None = None
    default_delay_reply_enabled: bool | None = None
    compression_threshold: int | None = Field(None, ge=1, le=100)
    idle_threshold_percent: int | None = Field(None, ge=1, le=99)
    compress_target_percent: int | None = Field(None, ge=1, le=99)


class ConvLogAgentSettingsBody(PydanticBaseModel):
    conversation_logs_limit: int | None = Field(None, ge=1, le=500)
    user_can_view_logs: bool | None = None


@router.get("/conversation-log/config")
async def get_conv_log_config(
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """获取对话日志全局配置"""
    from app.services.content.conversation_log_service import get_config_dict
    return await get_config_dict(SQLAlchemyContentRepository(db))


@router.put("/conversation-log/config")
async def update_conv_log_config(
    req: ConvLogConfigBody,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """更新对话日志全局配置"""
    from app.services.content.conversation_log_service import update_config
    try:
        result = await update_config(
            SQLAlchemyContentRepository(db),
            updated_by=admin["user_id"],
            max_conversation_logs=req.max_conversation_logs,
            default_user_conversation_logs=req.default_user_conversation_logs,
            default_user_log_access=req.default_user_log_access,
            default_delay_reply_enabled=req.default_delay_reply_enabled,
            compression_threshold=req.compression_threshold,
            idle_threshold_percent=req.idle_threshold_percent,
            compress_target_percent=req.compress_target_percent,
        )
        await _log_admin_action(
            db, admin["user_id"], "update_conv_log_config", "system", 1,
            req.model_dump(exclude_none=True),
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/conversation-log/agents/{agent_id}/settings")
async def get_agent_conv_log_settings(
    agent_id: int,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """获取某 AI 的对话日志设置"""
    from app.services.content.conversation_log_service import get_agent_log_settings
    try:
        return await get_agent_log_settings(SQLAlchemyContentRepository(db), agent_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.put("/conversation-log/agents/{agent_id}/settings")
async def update_agent_conv_log_settings(
    agent_id: int,
    req: ConvLogAgentSettingsBody,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """更新某 AI 的对话日志设置"""
    from app.services.content.conversation_log_service import update_agent_log_settings
    try:
        result = await update_agent_log_settings(
            SQLAlchemyContentRepository(db), agent_id,
            conversation_logs_limit=req.conversation_logs_limit,
            user_can_view_logs=req.user_can_view_logs,
        )
        await _log_admin_action(
            db, admin["user_id"], "update_agent_conv_log", "agent", agent_id,
            req.model_dump(exclude_none=True),
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/conversation-log/agents/{agent_id}/logs")
async def get_agent_conv_logs(
    agent_id: int,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """获取某 AI 的对话日志列表（管理员）"""
    from app.services.content.conversation_log_service import get_agent_logs
    try:
        return await get_agent_logs(SQLAlchemyContentRepository(db), agent_id, is_admin=True, limit=limit, offset=offset)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/conversation-log/agents/{agent_id}/logs/{log_id}")
async def get_agent_conv_log_detail(
    agent_id: int,
    log_id: int,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """获取单条对话日志详情（含完整 messages）"""
    from app.services.content.conversation_log_service import get_log_detail
    try:
        detail = await get_log_detail(SQLAlchemyContentRepository(db), log_id, is_admin=True)
        if detail is None:
            raise HTTPException(status_code=404, detail="日志不存在")
        return detail
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))


# ============================================================
# Token 用量分析（管理员）
# ============================================================

from datetime import datetime, timedelta, timezone as tz


@router.get("/admin/usage/global")
async def get_global_usage(
    days: int = Query(30, ge=1, le=365),
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """全站 token 消耗总览"""
    from app.services.content.conversation_log_service import get_admin_global_token_stats
    end_date = datetime.now(tz.utc).replace(tzinfo=None)
    start_date = end_date - timedelta(days=days)
    return await get_admin_global_token_stats(SQLAlchemyContentRepository(db), start_date, end_date)


@router.get("/admin/usage/by-user")
async def get_usage_by_user(
    days: int = Query(30, ge=1, le=365),
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """按用户分组的 token 消耗明细"""
    from app.services.content.conversation_log_service import get_admin_users_token_summary
    end_date = datetime.now(tz.utc).replace(tzinfo=None)
    start_date = end_date - timedelta(days=days)
    return await get_admin_users_token_summary(SQLAlchemyContentRepository(db), start_date, end_date)


@router.get("/admin/usage/agents/{agent_id}/daily")
async def get_agent_daily_usage_admin(
    agent_id: int,
    days: int = Query(30, ge=1, le=365),
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """获取单个 AI 每日 token 消耗分布"""
    from app.services.content.conversation_log_service import get_agent_token_daily
    end_date = datetime.now(tz.utc).replace(tzinfo=None)
    start_date = end_date - timedelta(days=days)
    return await get_agent_token_daily(SQLAlchemyContentRepository(db), agent_id, start_date, end_date)


# ══════════════════════════════════════════════════════════════
# v0.1.4: 系统监控指标
# ══════════════════════════════════════════════════════════════

@router.get("/admin/metrics")
async def get_system_metrics(
    hours: int = Query(24, ge=1, le=168),
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    获取系统性能指标：
    - live: 当前内存中的实时快照
    - timeline: 历史趋势（agent_metrics 表）
    - retention_days: 当前保留天数
    """
    from app.models.agent_metrics import AgentMetricsSnapshot
    from app.services.infrastructure.metrics_collector import metrics
    from datetime import timedelta as _td
    from sqlalchemy import select as _sel_m

    # 实时指标
    live = await metrics.snapshot()

    # 历史趋势
    cutoff = datetime.now(tz.utc).replace(tzinfo=None) - _td(hours=hours)
    result = await db.execute(
        _sel_m(AgentMetricsSnapshot)
        .where(AgentMetricsSnapshot.created_at >= cutoff)
        .order_by(AgentMetricsSnapshot.created_at.asc())
    )
    history = result.scalars().all()

    timeline = []
    for snap in history:
        sd = snap.snapshot_data or {}
        timeline.append({
            "at": snap.created_at.isoformat() if snap.created_at else None,
            "llm_calls": sd.get("llm", {}).get("total_calls", 0),
            "llm_avg_latency": sd.get("llm", {}).get("latency", {}).get("avg", 0),
            "llm_error_rate": sd.get("llm", {}).get("error_rate", 0),
            "messages_per_second": sd.get("messages", {}).get("per_second_last_60s", 0),
            "queue_depth": sd.get("queue", {}).get("max_depth", 0),
            "willingness": sd.get("willingness", {}),
            "errors": sd.get("errors", {}),
        })

    return {
        "live": live,
        "timeline": timeline,
        "hours": hours,
        "retention_days": settings.agent_metrics_retention_days,
    }


# ────────────────────────────────────────────
# Part B: 工具与技能管理（插件架构 + 全透明面板）
# ────────────────────────────────────────────

@router.get("/tools")
async def admin_get_tools(
    admin: dict = Depends(require_admin),
):
    """
    获取所有工具信息（管理面板「工具注册表」用）
    返回工具列表 + 技能段 + 总数
    """
    from app.tools.base import ToolRegistry
    return ToolRegistry.get_tools_info()


@router.get("/tools/backpack")
async def get_skill_backpack(
    admin: dict = Depends(require_admin),
    agent_id: int | None = None,
    db: AsyncSession = Depends(get_db),
):
    """
    获取技能背包视图（段+工具+元数据，面向用户/管理员的技能展示）。
    可选 agent_id：传入时标注每个工具在该 AI 当前状态下的可用性。
    """
    from app.tools.base import ToolRegistry
    segments = ToolRegistry.get_segments()

    # 如果提供了 agent_id，查询 AI 状态并标注工具可用性
    agent_state = None
    agent_thinking = None
    if agent_id is not None:
        from app.models.agent import Agent as AgentModel
        result = await db.execute(
            select(AgentModel).where(AgentModel.id == agent_id)
        )
        agent = result.scalar_one_or_none()
        if agent:
            agent_state = agent.state
            agent_thinking = agent.thinking_enabled
            # 获取该状态下允许的工具
            from app.services.skill.skill_engine import _is_delay_reply_allowed
            delay_allowed = await _is_delay_reply_allowed(db, agent)
            allowed_defs = ToolRegistry.get_allowed_tools(
                agent_state, agent_thinking, delay_allowed,
            )
            allowed_names = {t["function"]["name"] for t in allowed_defs}

            # 标注每个工具的可用性
            for seg_key, seg_data in segments.items():
                for tool in seg_data.get("tools", []):
                    tool["available_in_current_state"] = tool["name"] in allowed_names

    return {
        "segments": list(segments.values()),
        "agent_state": agent_state,
        "agent_thinking": agent_thinking,
    }


@router.get("/skills/agents/{agent_id}")
async def admin_get_agent_skills(
    agent_id: int,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    获取某个 AI 的所有思维技能（管理面板「AI 技能管理」用）
    """
    from app.models.agent import Agent
    from app.services.skill.skill_service import list_skills

    # 验证 AI 存在
    agent_result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = agent_result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail="AI 代理不存在")

    skills = await list_skills(db, agent_id)
    return {
        "agent_id": agent_id,
        "agent_name": agent.name,
        "skills": skills,
        "count": len(skills),
    }


class AgentSkillUpdate(BaseModel):
    """管理面板修改 AI 技能"""
    is_enabled: bool | None = Field(None, description="是否启用")
    name: str | None = Field(None, description="技能名称")
    config: dict | None = Field(None, description="技能配置")
    priority: int | None = Field(None, description="优先级")


@router.put("/skills/agents/{agent_id}/{skill_id}")
async def admin_update_agent_skill(
    agent_id: int,
    skill_id: int,
    body: AgentSkillUpdate,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    管理员修改某个 AI 的技能（启用/禁用/修改配置）
    """
    from app.services.skill.skill_service import update_skill, toggle_skill

    # 如果仅修改启用状态，用 toggle
    if len(body.model_dump(exclude_none=True)) == 1 and body.is_enabled is not None:
        return await toggle_skill(db, agent_id, skill_id, body.is_enabled)

    return await update_skill(
        db, agent_id, skill_id,
        name=body.name,
        config=body.config,
        is_enabled=body.is_enabled,
        priority=body.priority,
    )


# ══════════════════════════════════════════════════════════════
# v0.2.0 LLM 厂商预设
# ══════════════════════════════════════════════════════════════

@router.get("/provider-presets")
async def get_provider_presets(
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """获取所有 LLM 厂商预设 + 当前已配置的供应商列表"""
    from app.services.agent.provider_presets import get_all_presets
    from app.services.infrastructure.system_settings_service import get_providers

    presets = get_all_presets()
    current_providers = await get_providers(db)

    return {
        "presets": presets,
        "providers": current_providers,
    }


class FetchModelsBody(BaseModel):
    base_url: str
    api_key: str | None = None


@router.post("/provider-presets/fetch-models")
async def fetch_provider_models(
    body: FetchModelsBody,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """拉取某供应商的模型清单（管理页「获取模型」按钮）。

    key 优先用请求里带的一次性 key（表单里填、不保存）；没带就回退到**当前管理员
    自己保存的**那把 —— 供应商配置本身不存 key，key 是用户级的。
    探测策略/文案/脱敏全部复用 api_probe，这里只做"取 key + 转格式"。
    """
    from app.services.agent.api_probe import probe_provider

    key = body.api_key
    if not key:
        from app.services.infrastructure.user_credentials import user_api_key
        key = await user_api_key(db, admin["user_id"])

    # 管理员是这台机器的信任根：新加的私网供应商必须能先测再存，所以这里不设限
    probe = await probe_provider(body.base_url, key, allow_private=True)
    return {
        "ok": probe.ok,
        "message": probe.message,
        "models": [{"value": m, "label": m} for m in probe.models],
    }


class SaveProviderBody(BaseModel):
    name: str
    provider: str  # preset key 或 "manual"
    base_url: str | None = None
    chat_model: str | None = None
    work_model: str | None = None
    embedding_model: str | None = None
    model_options: list[dict] | None = None
    thinking_supported: bool | None = None
    is_default: bool = False
    index: int | None = None


@router.put("/provider-presets/save")
async def save_provider(
    body: SaveProviderBody,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """新增或更新一个 LLM 供应商配置（保存到 system_settings.provider_config 数组）"""
    import json
    from app.services.agent.provider_presets import get_preset
    from app.services.infrastructure.system_settings_service import get_providers
    from app.utils.pure.provider_config import build_provider_config, upsert_provider

    if body.provider == "manual":
        config = build_provider_config(
            name=body.name, provider_key="manual",
            base_url=body.base_url or "", chat_model=body.chat_model or "",
            work_model=body.work_model or "", embedding_model=body.embedding_model or "",
            model_options=body.model_options or [],
            thinking_supported=body.thinking_supported or False,
            is_default=body.is_default,
        )
    else:
        preset = get_preset(body.provider)
        if preset is None:
            raise HTTPException(400, f"未知厂商: {body.provider}")
        config = build_provider_config(
            name=body.name, provider_key=preset["key"],
            base_url=body.base_url or preset["base_url"],
            chat_model=body.chat_model or preset["chat_model"],
            work_model=body.work_model or preset["work_model"],
            embedding_model=body.embedding_model or preset["embedding_model"],
            model_options=body.model_options or preset["models"],
            thinking_supported=body.thinking_supported if body.thinking_supported is not None else preset["thinking_supported"],
            is_default=body.is_default,
        )

    providers = await get_providers(db)
    providers = upsert_provider(providers, config, body.index)

    from app.models.system_settings import SystemSettings
    result = await db.execute(select(SystemSettings).where(SystemSettings.id == 1))
    row = result.scalar_one_or_none()
    if row is None:
        row = SystemSettings(id=1)
        db.add(row)
    row.provider_config = json.dumps(providers, ensure_ascii=False)
    await db.commit()

    return {"message": f"已保存供应商 {body.name}", "providers": providers}


@router.delete("/provider-presets/{provider_name}")
async def delete_provider(
    provider_name: str,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """删除一个供应商配置"""
    import json
    from app.services.infrastructure.system_settings_service import get_providers
    from app.utils.pure.provider_config import remove_provider

    providers = await get_providers(db)
    new_list = remove_provider(providers, provider_name)
    if len(new_list) == len(providers):
        raise HTTPException(404, f"供应商 {provider_name} 不存在")

    from app.models.system_settings import SystemSettings
    result = await db.execute(select(SystemSettings).where(SystemSettings.id == 1))
    row = result.scalar_one_or_none()
    if row is None:
        row = SystemSettings(id=1)
        db.add(row)
    row.provider_config = json.dumps(new_list, ensure_ascii=False) if new_list else None
    await db.commit()

    return {"message": f"已删除供应商 {provider_name}", "providers": new_list}


# ══════════════════════════════════════════════════════════════
# 插件管理（服务插件启停，所有 AI 共享）
# ══════════════════════════════════════════════════════════════

# 导入 browser_plugin 触发自动注册（必须保留，否则插件列表为空）
from app.services.content.browser_plugin import BrowserPlugin  # noqa: F401
from app.services.infrastructure.plugin_registry import PluginRegistry


@router.get("/browser-status")
async def get_browser_status(admin: dict = Depends(require_admin)):
    """获取浏览器服务（Chromium CDP）运行状态"""
    plugin = PluginRegistry.get("browser")
    if plugin is None:
        return {"installed": False, "running": False, "port": None}
    return await plugin.get_status()


@router.get("/maintenance")
async def get_maintenance(admin: dict = Depends(require_admin)):
    return maintenance.state()


def _current_maintenance_mode() -> str | None:
    """返回当前维护模式: hard / soft / None（auto 归入 hard 处理）"""
    return maintenance.mode()


async def _broadcast_maintenance_update():
    """按当前实际模式广播维护状态（保存文案/应用预设后同步在线用户）"""
    mode = _current_maintenance_mode()
    if mode:
        await ws_manager.broadcast_to_all({"type": "maintenance_update", "mode": mode, "msg": maintenance.get_msg()})
    else:
        await ws_manager.broadcast_to_all({"type": "maintenance_update", "mode": "none"})


@router.post("/maintenance/hard")
async def toggle_hard(admin: dict = Depends(require_admin)):
    on = maintenance.toggle_hard()
    if on:
        await ws_manager.broadcast_to_all({"type": "maintenance_update", "mode": "hard", "msg": maintenance.get_msg()})
    else:
        await _broadcast_maintenance_update()
    return {"hard": on, "message": "硬维护已开启——API 返回 503" if on else "硬维护已关闭"}


@router.post("/maintenance/soft")
async def toggle_soft(admin: dict = Depends(require_admin)):
    on = maintenance.toggle_soft()
    if on:
        await ws_manager.broadcast_to_all({"type": "maintenance_update", "mode": "soft", "msg": maintenance.get_msg()})
    else:
        await _broadcast_maintenance_update()
    return {"soft": on, "message": "软维护已开启——API 正常，前端展示提示" if on else "软维护已关闭"}


class MaintenanceMsgBody(BaseModel):
    hard_title: str = "正在更新"
    hard_body: str = "服务器正在更新，稍等一下就好~"
    hard_color: str = "#f59e0b"
    hard_text_color: str = "#ffffff"
    hard_image: str = ""
    hard_style: str = "popup"
    soft_text: str = "服务器正在调整，功能可能偶尔不稳定"
    soft_color: str = "#f59e0b"
    soft_text_color: str = "#ffffff"
    soft_style: str = "banner"
    soft_once: bool = False


@router.get("/maintenance/msg")
async def get_maintenance_msg(admin: dict = Depends(require_admin)):
    return maintenance.get_msg()


@router.put("/maintenance/msg")
async def save_maintenance_msg(body: MaintenanceMsgBody, admin: dict = Depends(require_admin)):
    try:
        maintenance.save_msg(body.model_dump())
    except Exception:
        raise HTTPException(500, "维护文案保存失败：数据目录不可写")
    await _broadcast_maintenance_update()
    return {"ok": True, "message": "维护文本已保存"}


_PRESETS_FILE = os.path.join(settings.data_dir, "maintenance_presets.json")
_DEFAULT_PRESETS = [
    {"name": "服务器更新", "hard_title": "正在更新", "hard_body": "服务器正在更新，稍等一下就好~", "hard_color": "#f59e0b", "hard_text_color": "#ffffff", "hard_image": "", "hard_style": "popup", "soft_text": "服务器正在更新，功能可能偶尔不稳定", "soft_color": "#f59e0b", "soft_text_color": "#ffffff", "soft_style": "banner", "soft_once": False},
    {"name": "紧急维护", "hard_title": "紧急维护", "hard_body": "服务器突发故障，正在紧急抢修中，请稍后再来", "hard_color": "#ef4444", "hard_text_color": "#ffffff", "hard_image": "", "hard_style": "popup", "soft_text": "服务器正在紧急维护，可能会出现短暂不可用", "soft_color": "#ef4444", "soft_text_color": "#ffffff", "soft_style": "banner", "soft_once": False},
    {"name": "功能升级", "hard_title": "功能升级中", "hard_body": "正在升级新功能，马上就好~", "hard_color": "#8b5cf6", "hard_text_color": "#ffffff", "hard_image": "", "hard_style": "popup", "soft_text": "新功能部署中，部分功能可能暂时不可用", "soft_color": "#8b5cf6", "soft_text_color": "#ffffff", "soft_style": "banner", "soft_once": False},
    {"name": "网络波动", "hard_title": "网络波动", "hard_body": "网络不稳定，正在排查中", "hard_color": "#f59e0b", "hard_text_color": "#ffffff", "hard_image": "", "hard_style": "popup", "soft_text": "网络有些不稳定，正在处理", "soft_color": "#f59e0b", "soft_text_color": "#ffffff", "soft_style": "banner", "soft_once": False},
    {"name": "数据库维护", "hard_title": "数据库维护", "hard_body": "数据库正在优化，稍等片刻", "hard_color": "#3b82f6", "hard_text_color": "#ffffff", "hard_image": "", "hard_style": "popup", "soft_text": "数据库正在维护，涉及存储的功能可能受影响", "soft_color": "#3b82f6", "soft_text_color": "#ffffff", "soft_style": "banner", "soft_once": False},
    {"name": "性能优化", "hard_title": "性能优化中", "hard_body": "正在优化服务器性能，请稍等", "hard_color": "#10b981", "hard_text_color": "#ffffff", "hard_image": "", "hard_style": "popup", "soft_text": "服务器性能调优中，体验可能略有影响", "soft_color": "#10b981", "soft_text_color": "#ffffff", "soft_style": "banner", "soft_once": False},
]

def _load_presets():
    try:
        if os.path.exists(_PRESETS_FILE):
            with open(_PRESETS_FILE) as f:
                return json.loads(f.read())
    except Exception:
        pass
    try:
        os.makedirs(os.path.dirname(_PRESETS_FILE), exist_ok=True)
        with open(_PRESETS_FILE, "w") as f:
            f.write(json.dumps(_DEFAULT_PRESETS, ensure_ascii=False))
    except Exception:
        pass
    return list(_DEFAULT_PRESETS)


@router.get("/maintenance/presets")
async def list_presets(admin: dict = Depends(require_admin)):
    return {"presets": _load_presets()}


@router.post("/maintenance/presets/apply")
async def apply_preset(admin: dict = Depends(require_admin), name: str = ""):
    p = next((p for p in _load_presets() if p["name"] == name), None)
    if not p: raise HTTPException(404, f"预设 {name} 不存在")
    merged = {
        "hard_title": p.get("hard_title", "正在更新"),
        "hard_body": p.get("hard_body", "服务器正在更新，稍等一下就好~"),
        "hard_color": p.get("hard_color", "#f59e0b"),
        "hard_text_color": p.get("hard_text_color", "#ffffff"),
        "hard_image": p.get("hard_image", ""),
        "hard_style": p.get("hard_style", "popup"),
        "soft_text": p.get("soft_text", "服务器正在调整，功能可能偶尔不稳定"),
        "soft_color": p.get("soft_color", "#f59e0b"),
        "soft_text_color": p.get("soft_text_color", "#ffffff"),
        "soft_style": p.get("soft_style", "banner"),
        "soft_once": p.get("soft_once", False),
    }
    try:
        maintenance.save_msg(merged)
    except Exception:
        raise HTTPException(500, "应用预设失败：数据目录不可写")
    await _broadcast_maintenance_update()
    return {"ok": True, "message": f"已应用预设「{name}」", "msg": merged}


@router.delete("/maintenance/presets/{name}")
async def delete_preset(name: str, admin: dict = Depends(require_admin)):
    presets = _load_presets()
    new_list = [p for p in presets if p["name"] != name]
    if len(new_list) == len(presets): raise HTTPException(404, f"预设 {name} 不存在")
    with open(_PRESETS_FILE, "w") as f: f.write(json.dumps(new_list, ensure_ascii=False))
    return {"ok": True, "message": f"已删除预设「{name}」"}


class MaintenancePresetBody(BaseModel):
    name: str
    hard_title: str = "正在更新"
    hard_body: str = "服务器正在更新，稍等一下就好~"
    hard_color: str = "#f59e0b"
    hard_text_color: str = "#ffffff"
    hard_image: str = ""
    hard_style: str = "popup"
    soft_text: str = "服务器正在调整，功能可能偶尔不稳定"
    soft_color: str = "#f59e0b"
    soft_text_color: str = "#ffffff"
    soft_style: str = "banner"
    soft_once: bool = False


@router.post("/maintenance/presets")
async def add_preset(body: MaintenancePresetBody, admin: dict = Depends(require_admin)):
    """保存预设（同名覆盖，避免先删后建的数据丢失）"""
    presets = _load_presets()
    data = body.model_dump()
    replaced = False
    for i, p in enumerate(presets):
        if p["name"] == body.name:
            presets[i] = data
            replaced = True
            break
    if not replaced:
        presets.append(data)
    try:
        os.makedirs(os.path.dirname(_PRESETS_FILE), exist_ok=True)
        with open(_PRESETS_FILE, "w") as f: f.write(json.dumps(presets, ensure_ascii=False))
    except Exception:
        raise HTTPException(500, f"预设保存失败：{_PRESETS_FILE} 不可写")
    return {"ok": True, "message": f"已{'覆盖' if replaced else '添加'}预设「{body.name}」"}


# 维护图片库（独立于预设）
@router.get("/maintenance/images")
async def list_images(admin: dict = Depends(require_admin)):
    return {"images": maintenance.list_images()}


@router.post("/maintenance/images")
async def add_image(admin: dict = Depends(require_admin), url: str = ""):
    if not url: raise HTTPException(400, "缺少 url")
    return {"ok": True, "images": maintenance.add_image(url)}


@router.delete("/maintenance/images")
async def del_image(admin: dict = Depends(require_admin), url: str = ""):
    if not url: raise HTTPException(400, "缺少 url")
    return {"ok": True, "images": maintenance.remove_image(url)}


@router.post("/plugins/browser/test")
async def test_browser_connection(
    admin: dict = Depends(require_admin),
):
    """测试浏览器是否能访问公网——用 CDP 打开百度，返回结果或报错"""
    import urllib.request
    import urllib.error

    # 先检查 CDP 是否在运行
    try:
        cdp_resp = urllib.request.urlopen("http://127.0.0.1:9222/json/version", timeout=5)
        cdp_info = json.loads(cdp_resp.read())
    except Exception as e:
        return {"ok": False, "error": f"Chromium CDP 未运行: {e}", "step": "cdp-check"}

    # 检查进程是否存活
    import subprocess, os
    chrome_alive = subprocess.run(["pgrep", "-f", "chromium"], capture_output=True, text=True).stdout.strip()
    chrome_pids = chrome_alive.replace("\n", ",")

    # 用已有页面
    try:
        pages_resp = urllib.request.urlopen("http://127.0.0.1:9222/json", timeout=5)
        pages = json.loads(pages_resp.read())
        page = next((p for p in pages if p.get("type") == "page"), pages[0] if pages else None)
        if not page:
            return {"ok": False, "error": "没有可用页面", "step": "create-page"}
        ws_url = page.get("webSocketDebuggerUrl", "")
    except Exception as e:
        return {"ok": False, "error": f"获取CDP页面失败: {e}", "step": "create-page"}

    if not ws_url:
        return {"ok": False, "error": "无法获取 CDP WebSocket URL", "step": "ws-url"}

    # 通过 WebSocket 发送 navigate 命令
    try:
        import websockets

        msgs_log = []
        async with websockets.connect(ws_url, max_size=2**20, close_timeout=5) as ws:
            for domain in ("Page", "Runtime", "Network"):
                await ws.send(json.dumps({"id": domain, "method": f"{domain}.enable"}))
                try: await asyncio.wait_for(ws.recv(), timeout=2)
                except: pass
            # 直接导航到 example.com
            await ws.send(json.dumps({"id": 1, "method": "Page.navigate", "params": {"url": "http://example.com"}}))
            nav_result = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
            page_title = ""
            for _ in range(20):
                try:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
                except TimeoutError: break
                m = msg.get("method", ""); p = msg.get("params", {})
                e = p.get("errorText", "")
                if m == "Network.requestWillBeSent":
                    msgs_log.append(f"REQ:{p.get('request',{}).get('url','?')[:60]}")
                elif m == "Network.responseReceived":
                    msgs_log.append(f"RESP:{p.get('response',{}).get('status','?')}")
                elif m == "Network.loadingFailed":
                    msgs_log.append(f"FAIL:{e}")
                elif m == "Network.loadingFinished":
                    msgs_log.append(f"DONE")
                elif m == "Page.loadEventFired":
                    await ws.send(json.dumps({"id": 2, "method": "Runtime.evaluate", "params": {"expression": "document.title"}}))
                    title_msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                    page_title = title_msg.get("result", {}).get("result", {}).get("value", "")
                    msgs_log.append(f"TITLE:{page_title}")
                    break
    except TimeoutError:
        return {"ok": False, "error": "访问超时——网络不通或 DNS 解析失败", "step": "navigate", "cdp_msgs": msgs_log}
    except Exception as e:
        return {"ok": False, "error": f"CDP 通信失败: {e}", "step": "navigate", "cdp_msgs": msgs_log}

    error = nav_result.get("result", {}).get("errorText", "")
    if error:
        return {"ok": False, "error": f"浏览器导航失败: {error}", "step": "navigate-result"}

    return {
        "ok": True,
        "message": f"dataURL标题={data_title or '?'} | fetch={fetch_result} | 页面标题={page_title or '无'}{' CDP:' + ','.join(msgs_log) if not page_title else ''}",
        "page_title": page_title or "",
        "cdp_version": cdp_info.get("Browser", "unknown"),
    }


@router.get("/plugins")
async def list_plugins(
    admin: dict = Depends(require_admin),
):
    """列出所有可选插件及运行状态"""
    plugins = await PluginRegistry.get_status_all()
    return {"plugins": plugins}


@router.post("/plugins/{plugin_key}/start")
async def start_plugin(
    plugin_key: str,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """启动插件服务（plugin_key 是注册表 key：单实例=插件 id，多实例=插件 id:实例名）

    启停逻辑住在 runtime_control：用户给自己的 AI 开通道走的是同一段代码，
    两边各写一份迟早不一致。
    """
    from app.services.plugin import runtime_control

    try:
        return await runtime_control.start_instance(db, plugin_key)
    except runtime_control.UnknownInstance:
        raise HTTPException(404, f"未知插件实例: {plugin_key}")
    except runtime_control.StartFailed as e:
        # 起不来时把插件自己记的原因带出去：管理页要看得到"为什么失败"（如未配置凭据）
        raise HTTPException(500, str(e))


@router.post("/plugins/{plugin_key}/stop")
async def stop_plugin(
    plugin_key: str,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """停止插件服务（plugin_key 同上）"""
    from app.services.plugin import runtime_control

    try:
        return await runtime_control.stop_instance(db, plugin_key)
    except runtime_control.UnknownInstance:
        raise HTTPException(404, f"未知插件实例: {plugin_key}")


# ══════════════════════════════════════════════════════════════
# B站助手配置
# ══════════════════════════════════════════════════════════════


@router.get("/bilibili-sessdata")
async def get_bilibili_sessdata(
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """获取 B站 SESSDATA（已脱敏）"""
    from app.models.system_settings import SystemSettings
    result = await db.execute(select(SystemSettings).where(SystemSettings.id == 1))
    row = result.scalar_one_or_none()
    if row is None or not row.system_prompt_overrides:
        return {"configured": False, "sessdata_masked": None}
    raw = row.system_prompt_overrides.get("bilibili", {}).get("sessdata", "")
    masked = raw[:4] + "****" + raw[-4:] if len(raw) > 8 else None
    return {"configured": bool(raw), "sessdata_masked": masked}


@router.put("/bilibili-sessdata")
async def set_bilibili_sessdata(
    body: dict,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """设置 B站 SESSDATA"""
    sessdata = body.get("sessdata", "")
    from app.models.system_settings import SystemSettings
    result = await db.execute(select(SystemSettings).where(SystemSettings.id == 1))
    row = result.scalar_one_or_none()
    if row is None:
        row = SystemSettings(id=1)
        db.add(row)
    overrides = row.system_prompt_overrides or {}
    if "bilibili" not in overrides:
        overrides["bilibili"] = {}
    overrides["bilibili"]["sessdata"] = sessdata
    row.system_prompt_overrides = overrides
    await db.commit()
    masked = sessdata[:4] + "****" + sessdata[-4:] if len(sessdata) > 8 else ""
    return {"message": "B站 SESSDATA 已更新", "sessdata_masked": masked, "configured": bool(sessdata)}


@router.post("/blocks/{block_id}/update")
async def update_world_block(
    block_id: str,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """批量更新积木：所有已应用该积木的世界重新 apply（跳过 diy/ 用户定制），
    并给每个世界写懒通知（下次对话注入世界 AI 上下文）。"""
    from app.services.world.world_blocks import update_block_for_all_worlds
    try:
        result = await update_block_for_all_worlds(db, block_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await db.commit()
    return result


@router.post("/blocks/{block_id}/update")
async def update_world_block(
    block_id: str,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """批量更新积木：所有已应用该积木的世界重新 apply（跳过 diy/ 用户定制），
    并给每个世界写懒通知（下次对话注入世界 AI 上下文）。"""
    from app.services.world.world_blocks import update_block_for_all_worlds
    try:
        result = await update_block_for_all_worlds(db, block_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await db.commit()
    return result


@router.get("/api-doc-sections")
async def list_api_doc_sections(
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """接口文档分区（DB 快照 + md 源对比状态），供管理表单编辑"""
    from app.services.world.world_api_docs import get_sections, _discover_sections
    sections = await get_sections(db)
    docs = {s["id"]: s for s in _discover_sections()}
    for s in sections:
        d = docs.get(s["id"])
        s["doc_title"] = d["title"] if d else None
        s["doc_intro"] = d["intro"] if d else None
        s["title_changed"] = bool(d and d["title"] != s["title"])
        s["intro_changed"] = bool(d and d["intro"] != s["intro"])
    return {"sections": sections}


@router.post("/api-doc-sections/sync-from-docs")
async def sync_api_doc_sections_from_docs(
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """「从文档中更新」：md 源 → DB 快照全量同步（新增/更新/删除，以 md 为准）"""
    from app.services.world.world_api_docs import sync_sections_from_docs
    result = await sync_sections_from_docs(db)
    return result


@router.put("/api-doc-sections")
async def save_api_doc_sections(
    body: dict,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """保存表单：更新 DB 快照（即时生效）；write_back=true 时同步写回 md 源"""
    from app.services.world.world_api_docs import save_sections
    items = body.get("sections") or []
    write_back = bool(body.get("write_back"))
    result = await save_sections(db, items, write_back=write_back)
    return result


# ═══════════════════════════════════════════════════════════════
# Embedding 提供方配置（DB 覆盖 env，前端图形化修改）
# ═══════════════════════════════════════════════════════════════

from pydantic import BaseModel as _BaseModel  # noqa: E402


class EmbeddingConfigBody(_BaseModel):
    """可前端修改的 embedding 配置（api_key 为明文提交，服务端加密存储）"""
    embedding_backend: str | None = None
    embedding_base_url: str | None = None
    embedding_api_key: str | None = None
    embedding_model: str | None = None
    embedding_dimension: int | None = None


@router.get("/embedding-config")
async def get_embedding_config(
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """获取当前生效的 embedding 配置（DB 覆盖 + env 兜底，api_key 脱敏）"""
    from app.services.infrastructure.embedding_config_service import get_effective_config
    return await get_effective_config(db)


@router.put("/embedding-config")
async def save_embedding_config(
    body: EmbeddingConfigBody,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """保存 embedding 配置（DB 持久化 + 缓存热更新，api_key 加密存储）"""
    from app.services.infrastructure.embedding_config_service import save_db_config
    values = body.model_dump(exclude_none=True)
    try:
        saved = await save_db_config(db, values)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return {"message": "已保存", "config": saved}


@router.delete("/embedding-config")
async def reset_embedding_config(
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """恢复默认：清除 DB 覆盖，回到环境变量配置"""
    from app.services.infrastructure.embedding_config_service import clear_db_config
    await clear_db_config(db)
    return {"message": "已恢复默认（回到环境变量配置）"}


@router.post("/embedding-config/test")
async def test_embedding_config(
    admin: dict = Depends(require_admin),
):
    """测试当前 embedding 配置：实际调一次 embed，返回维度"""
    from app.embedding_providers import get_embedding_provider
    provider = get_embedding_provider()
    if not provider.is_available():
        raise HTTPException(status_code=400, detail="当前配置不完整（缺 base_url/model）")
    vec = await provider.embed("连接测试")
    if not vec:
        raise HTTPException(status_code=502, detail="Embedding 调用失败，请检查端点/模型/密钥")
    return {"dimension": len(vec), "provider": provider.name}


# ═══════════════════════════════════════════════════════════════
# 通用配置组 API（第二批：任意配置组前端图形化）
# ═══════════════════════════════════════════════════════════════

@router.get("/configs")
async def list_config_groups(
    admin: dict = Depends(require_admin),
):
    """列出所有可前端修改的配置组及其 schema（前端按 type 自动渲染表单）"""
    from app.services.infrastructure.app_config_service import CONFIG_GROUPS
    return {
        "groups": [
            {
                "key": key,
                "label_key": schema.get("label_key", ""),
                "hint_key": schema.get("hint_key", ""),
                "fields": schema["fields"],
            }
            for key, schema in CONFIG_GROUPS.items()
        ]
    }


@router.get("/configs/{group}")
async def get_config_group(
    group: str,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """获取某配置组当前生效配置（DB 覆盖 + env 兜底，敏感字段脱敏）"""
    from app.services.infrastructure.app_config_service import get_effective_config
    try:
        return await get_effective_config(db, group)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.put("/configs/{group}")
async def save_config_group(
    group: str,
    body: dict,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """保存某配置组（DB 持久化 + 缓存热更新；敏感字段加密存储）"""
    from app.services.infrastructure.app_config_service import save_group_config
    try:
        saved = await save_group_config(db, group, body)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"message": "已保存", "config": saved}


@router.delete("/configs/{group}")
async def reset_config_group(
    group: str,
    admin: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """恢复某配置组默认：清除 DB 覆盖，回到环境变量配置"""
    from app.services.infrastructure.app_config_service import clear_group_config
    try:
        await clear_group_config(db, group)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"message": "已恢复默认（回到环境变量配置）"}
