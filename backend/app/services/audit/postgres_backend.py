"""
PostgreSQL 审计日志存储后端

- 哈希链防篡改（SHA256）
- 自动清理策略
- 哈希链完整性验证
"""
import hashlib
import logging
from datetime import datetime
from typing import Any, Optional
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSession as DBSession
from app.models.system_log import SystemLog
from app.repositories.audit_repo import AuditRepository, SQLAlchemyAuditRepository
from . import AuditStorageBackend
from app.utils.pure.timeutil import utc_now

logger = logging.getLogger(__name__)


def _ensure_repo(db_or_repo):
    """兼容旧调用：传入 AsyncSession 时包装为 SQLAlchemyAuditRepository。"""
    if isinstance(db_or_repo, AsyncSession):
        return SQLAlchemyAuditRepository(db_or_repo)
    return db_or_repo


def _compute_hash(
    prev_hash: Optional[str],
    created_at: datetime,
    log_type: str,
    operator_type: str,
    operator_id: int,
    target_type: str,
    target_id: Optional[int],
    success: bool,
    old_value: Any,
    new_value: Any,
    ip_address: Optional[str],
) -> str:
    parts = [
        prev_hash or "",
        created_at.isoformat() if created_at else "",
        log_type,
        operator_type,
        str(operator_id),
        target_type,
        str(target_id or ""),
        "1" if success else "0",
        str(old_value or ""),
        str(new_value or ""),
        ip_address or "",
    ]
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class PostgresAuditBackend(AuditStorageBackend):

    def __init__(self):
        pass

    async def write(self, entry: dict, db: AsyncSession | None = None) -> None:
        db = _ensure_repo(db)
        if db is None:
            raise ValueError("PostgresAuditBackend.write 需要 db 参数")
        prev = (
            await db.execute(
                select(SystemLog.hash)
                .order_by(SystemLog.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

        now = utc_now()
        current_hash = _compute_hash(
            prev_hash=prev,
            created_at=now,
            log_type=entry["log_type"],
            operator_type=entry["operator_type"],
            operator_id=entry["operator_id"],
            target_type=entry["target_type"],
            target_id=entry.get("target_id"),
            success=entry.get("success", True),
            old_value=entry.get("old_value"),
            new_value=entry.get("new_value"),
            ip_address=entry.get("ip_address"),
        )

        log = SystemLog(
            log_type=entry["log_type"],
            operator_type=entry["operator_type"],
            operator_id=entry["operator_id"],
            target_type=entry["target_type"],
            target_id=entry.get("target_id"),
            success=entry.get("success", True),
            error_message=entry.get("error_message"),
            ip_address=entry.get("ip_address"),
            old_value=entry.get("old_value"),
            new_value=entry.get("new_value"),
            details=entry.get("details", {}),
            prev_hash=prev,
            hash=current_hash,
            created_at=now,
        )
        db.add(log)
        if entry.get("_flush", True):
            await db.flush()

    async def query(self, filters: dict, page: int = 1, page_size: int = 50, db: AsyncSession | None = None) -> dict:
        db = _ensure_repo(db)
        if db is None:
            raise ValueError("PostgresAuditBackend.query 需要 db 参数")
        query = select(SystemLog).order_by(SystemLog.created_at.desc())
        if filters.get("log_type"):
            query = query.where(SystemLog.log_type == filters["log_type"])
        if filters.get("operator_type"):
            query = query.where(SystemLog.operator_type == filters["operator_type"])
        if filters.get("start_date"):
            query = query.where(SystemLog.created_at >= filters["start_date"])
        if filters.get("end_date"):
            query = query.where(SystemLog.created_at <= filters["end_date"])
        if filters.get("success") is not None:
            query = query.where(SystemLog.success == filters["success"])

        offset = (page - 1) * page_size
        result = await db.execute(query.offset(offset).limit(page_size))
        items = [log.to_dict() for log in result.scalars().all()]

        return {"items": items, "total": 0, "page": page, "page_size": page_size}

    async def cleanup(self, before: str, batch_size: int = 5000, db: AsyncSession | None = None) -> dict:
        db = _ensure_repo(db)
        if db is None:
            raise ValueError("PostgresAuditBackend.cleanup 需要 db 参数")
        total_deleted = 0
        while True:
            result = await db.execute(
                delete(SystemLog)
                .where(SystemLog.created_at < before)
                .limit(batch_size)
            )
            deleted = result.rowcount
            total_deleted += deleted
            if deleted < batch_size:
                break
        return {"deleted": total_deleted, "cutoff": before}

    async def verify_chain(self, limit: int = 1000, db: AsyncSession | None = None) -> dict:
        db = _ensure_repo(db)
        if db is None:
            raise ValueError("PostgresAuditBackend.verify_chain 需要 db 参数")
        result = await db.execute(
            select(SystemLog).order_by(SystemLog.id.asc()).limit(limit)
        )
        logs = result.scalars().all()

        broken = None
        for i, entry in enumerate(logs):
            expected = _compute_hash(
                prev_hash=entry.prev_hash,
                created_at=entry.created_at,
                log_type=entry.log_type,
                operator_type=entry.operator_type,
                operator_id=entry.operator_id,
                target_type=entry.target_type,
                target_id=entry.target_id,
                success=entry.success,
                old_value=entry.old_value,
                new_value=entry.new_value,
                ip_address=entry.ip_address,
            )
            if expected != entry.hash:
                broken = entry.id
                break

        return {"valid": broken is None, "checked": len(logs), "first_broken": broken}
