"""
系统设置仓库接口（Protocol）+ SQLAlchemy 实现。
"""
from typing import Protocol
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.group import DEFAULT_CONCURRENT_AI_LIMIT
from app.models.system_settings import SystemSettings


class SystemSettingsRepository(Protocol):
    async def get_settings(self) -> dict: ...


class SQLAlchemySystemSettingsRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def _get_or_create(self) -> SystemSettings:
        result = await self.session.execute(select(SystemSettings).where(SystemSettings.id == 1))
        row = result.scalar_one_or_none()
        if row is None:
            row = SystemSettings(id=1, default_language="en")
            self.session.add(row)
            await self.session.flush()
            await self.session.refresh(row)
        return row

    async def get_settings(self) -> dict:
        row = await self._get_or_create()
        return {
            "id": row.id,
            "default_language": row.default_language,
            "default_platform_credit": row.default_platform_credit or 0,
            "default_file_quota_mb": row.default_file_quota_mb,
            "default_concurrent_ai_limit": row.default_concurrent_ai_limit or DEFAULT_CONCURRENT_AI_LIMIT,
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
