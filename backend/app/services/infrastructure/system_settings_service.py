"""
系统设置服务
单行表模式（id=1），懒初始化
"""
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.group import DEFAULT_CONCURRENT_AI_LIMIT
from app.models.system_settings import SystemSettings
from app.repositories.infra_repo import InfraRepository, SQLAlchemyInfraRepository
from app.utils.pure.provider_config import (
    normalize_legacy_config, find_default_provider,
    find_provider_by_name, find_provider_for_pool_key,
)

logger = logging.getLogger(__name__)


def _ensure_repo(db_or_repo):
    """兼容旧调用：传入 AsyncSession 时包装为 SQLAlchemyInfraRepository。"""
    if isinstance(db_or_repo, AsyncSession):
        return SQLAlchemyInfraRepository(db_or_repo)
    return db_or_repo


async def _get_or_create(db: AsyncSession) -> SystemSettings:
    """获取系统设置行，不存在则创建（懒初始化）"""
    db = _ensure_repo(db)
    result = await db.execute(select(SystemSettings).where(SystemSettings.id == 1))
    row = result.scalar_one_or_none()
    if row is None:
        row = SystemSettings(id=1, default_language="en")
        db.add(row)
        await db.flush()
        await db.refresh(row)
        logger.info("已初始化 system_settings（默认语言=en）")
    return row


async def get_default_provider(db: AsyncSession) -> dict:
    """获取**默认**厂商配置：数组里第一个 is_default 的项（没有则第一项），空配置返回 {}。

    ⚠️ 返回的是**单个 dict**，用于"取默认值"（如默认 base_url）。
    需要按 base_url / name **匹配**某个厂商时，必须用 get_providers()（返回 list[dict]）——
    把本函数的 dict 直接喂给 find_provider_by_base_url 之类，迭代出来是字符串键，会抛 AttributeError。
    """
    providers = await get_providers(db)
    return find_default_provider(providers) or {}


async def get_providers(db: AsyncSession) -> list[dict]:
    """获取所有 LLM 厂商配置数组（纯函数 normalize_legacy_config 处理旧格式兼容）"""
    db = _ensure_repo(db)
    from app.models.system_settings import SystemSettings
    result = await db.execute(select(SystemSettings).where(SystemSettings.id == 1))
    row = result.scalar_one_or_none()
    if row is None:
        return []
    return normalize_legacy_config(row.provider_config)


async def get_provider_by_name(db: AsyncSession, name: str) -> dict | None:
    """按名称查找单个供应商配置"""
    providers = await get_providers(db)
    return find_provider_by_name(providers, name)


async def get_provider_for_pool_key(db: AsyncSession, pool_key) -> dict:
    """根据池 Key 获取对应的供应商配置（纯函数 find_provider_for_pool_key）"""
    providers = await get_providers(db)
    provider_name = getattr(pool_key, "provider_name", None)
    api_base_url = getattr(pool_key, "api_base_url", None)
    provider = find_provider_for_pool_key(providers, provider_name, api_base_url) or {}
    # 返回全局默认模型配置
    if provider:
        provider["global_default_chat_model"] = provider.get("global_default_chat_model")
        provider["global_default_work_model"] = provider.get("global_default_work_model")
    return provider


async def get_settings(db: AsyncSession) -> dict:
    """获取系统设置"""
    db = _ensure_repo(db)
    row = await _get_or_create(db)
    return {
        "id": row.id,
        "default_language": row.default_language,
        "default_platform_credit": row.default_platform_credit or 0,
        "system_prompt_overrides": row.system_prompt_overrides,
        "system_prompt_order": row.system_prompt_order,
        "federation_sync_interval_minutes": row.federation_sync_interval_minutes,
        "default_file_quota_mb": row.default_file_quota_mb,
        "default_concurrent_ai_limit": row.default_concurrent_ai_limit or DEFAULT_CONCURRENT_AI_LIMIT,
        "updated_by": row.updated_by,
        "updated_at": str(row.updated_at) if row.updated_at else None,
        # v0.2.0 邮箱认证（公开字段）
        "login_providers": getattr(row, "login_providers", ["direct"]) or ["direct"],
        "require_email_verification": getattr(row, "require_email_verification", False) or False,
        "registration_enabled": getattr(row, "registration_enabled", True) if getattr(row, "registration_enabled", True) is not None else True,
        "smtp_config": getattr(row, "smtp_config", None),
        "email_templates": getattr(row, "email_templates", None),
        "provider_config": getattr(row, "provider_config", None),
        "geoip_provider_url": getattr(row, "geoip_provider_url", None),
        "audit_user_actions": getattr(row, "audit_user_actions", False) or False,
        "audit_log_retention_days": getattr(row, "audit_log_retention_days", 90) or 90,
        "message_retention_days": getattr(row, "message_retention_days", 0) or 0,
        "daily_backup_enabled": bool(getattr(row, "daily_backup_enabled", False)),
        "daily_backup_keep": getattr(row, "daily_backup_keep", 7) or 7,
        "world_preset_suggestions": getattr(row, "world_preset_suggestions", None),
    }


async def update_settings(
    db: AsyncSession,
    default_language: str | None = None,
    default_platform_credit: int | None = None,
    default_file_quota_mb: int | None = None,
    default_concurrent_ai_limit: int | None = None,
    registration_enabled: bool | None = None,
    geoip_provider_url: str | None = None,
    audit_user_actions: bool | None = None,
    audit_log_retention_days: int | None = None,
    message_retention_days: int | None = None,
    daily_backup_enabled: bool | None = None,
    daily_backup_keep: int | None = None,
    world_preset_suggestions: list[str] | None = None,
    updated_by: int | None = None,
) -> dict:
    """
    更新系统设置（仅更新传入的非空字段）。

    若设置 default_platform_credit > 0，需验证池中至少有一个 active Key。
    修改 default_platform_credit 会批量更新所有用户的 platform_gifted_credit。
    修改 default_file_quota_mb 会同步调整所有用户的 file_quota_mb（保留兑换码加成）。
    """
    db = _ensure_repo(db)
    row = await _get_or_create(db)

    if default_language is not None:
        if default_language not in ("zh", "en", "ja"):
            raise ValueError("不支持的语言，仅支持 zh / en / ja")
        row.default_language = default_language

    if default_platform_credit is not None:
        # 验证：若 > 0，池中必须有 active Key
        if default_platform_credit > 0:
            from app.models.api_key_pool import ApiKeyPool
            key_result = await db.execute(
                select(ApiKeyPool).where(ApiKeyPool.is_active == True).limit(1)
            )
            if key_result.scalar_one_or_none() is None:
                raise ValueError("API Key 池中没有可用的 Key，无法启用平台赠送额度")

        # 计算 delta → 批量更新所有用户
        old_value = row.default_platform_credit or 0
        delta = default_platform_credit - old_value
        row.default_platform_credit = default_platform_credit

        if delta != 0:
            from app.models.user import User as UserModel
            from sqlalchemy import text
            await db.execute(
                text(
                    "UPDATE users SET platform_gifted_credit = platform_gifted_credit + :delta"
                ),
                {"delta": delta},
            )
            logger.info(
                f"  平台赠送额度变更: {old_value} → {default_platform_credit} "
                f"(delta={delta:+d})，已批量更新所有用户"
            )

    if default_file_quota_mb is not None:
        old_value = row.default_file_quota_mb or 100
        delta = default_file_quota_mb - old_value
        row.default_file_quota_mb = default_file_quota_mb

        if delta != 0:
            from app.models.user import User as UserModel
            from sqlalchemy import text
            # 调整所有用户的 file_quota_mb（基数额度），兑换码加成部分不变
            await db.execute(
                text(
                    "UPDATE users SET file_quota_mb = file_quota_mb + :delta "
                    "WHERE type = 'human'"
                ),
                {"delta": delta},
            )
            logger.info(
                f"  文件配额基数变更: {old_value}MB → {default_file_quota_mb}MB "
                f"(delta={delta:+d})，已批量更新所有人类用户"
            )

    if default_concurrent_ai_limit is not None:
        row.default_concurrent_ai_limit = default_concurrent_ai_limit
        logger.info(f"  默认 AI 并发数设为: {default_concurrent_ai_limit}")

    if registration_enabled is not None:
        row.registration_enabled = registration_enabled
        logger.info(f"  注册通道: {'开启' if registration_enabled else '关闭'}")

    if geoip_provider_url is not None:
        row.geoip_provider_url = geoip_provider_url or None
        logger.info(f"  IP 地理位置查询后端: {geoip_provider_url or '（默认 ip-api.com）'}")

    if audit_user_actions is not None:
        row.audit_user_actions = audit_user_actions
        logger.info(f"  用户行为日志: {'开启' if audit_user_actions else '关闭'}")

    if audit_log_retention_days is not None:
        row.audit_log_retention_days = audit_log_retention_days
        logger.info(f"  审计日志保留天数: {audit_log_retention_days}")

    if message_retention_days is not None:
        row.message_retention_days = message_retention_days
        logger.info(f"  消息保留天数: {message_retention_days}（0=永久）")

    if daily_backup_enabled is not None:
        row.daily_backup_enabled = daily_backup_enabled
        logger.info(f"  每日备份: {'开启' if daily_backup_enabled else '关闭'}")

    if daily_backup_keep is not None:
        row.daily_backup_keep = daily_backup_keep

    if world_preset_suggestions is not None:
        row.world_preset_suggestions = [str(q).strip()[:40] for q in world_preset_suggestions if str(q).strip()][:10]
        logger.info(f"  备份保留份数: {daily_backup_keep}")

    if updated_by is not None:
        row.updated_by = updated_by

    await db.flush()
    await db.refresh(row)
    logger.info(f"系统设置已更新: default_language={row.default_language}, "
                f"default_platform_credit={row.default_platform_credit}")
    return await get_settings(db)
