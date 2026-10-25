"""审计日志清理必须真的删得掉、而且真的落库。

2026-10-25 修：`delete(...).limit()` 在 SQLAlchemy 上是 AttributeError（PostgreSQL 原生也没有
DELETE ... LIMIT），bootstrap 的清理循环每 5 分钟报一次 WARN、审计表从没被清过；
就算语句能跑通，那个循环也没有 commit，删了也会被回滚。

所以这里从两头钉住：旧记录删得掉、新记录不许动、换一个会话（= 新连接）也查不到旧记录。
"""
from __future__ import annotations

from datetime import timedelta


async def _seed(when) -> int:
    from app.database import async_session
    from app.models.system_log import SystemLog

    async with async_session() as db:
        row = SystemLog(
            log_type="system", operator_type="system", operator_id=0,
            target_type="audit-cleanup-test", success=True, hash="0" * 64,
            created_at=when, details={},
        )
        db.add(row)
        await db.commit()
        return int(row.id)


async def _drop(*ids: int) -> None:
    from sqlalchemy import delete

    from app.database import async_session
    from app.models.system_log import SystemLog

    async with async_session() as db:
        await db.execute(delete(SystemLog).where(SystemLog.id.in_(list(ids))))
        await db.commit()


async def test_cleanup_deletes_old_rows_and_keeps_recent_ones(migrated_db):
    from app.database import async_session
    from app.repositories.audit_repo import SQLAlchemyAuditRepository
    from app.services.audit.postgres_backend import PostgresAuditBackend
    from app.utils.pure.timeutil import utc_now

    now = utc_now()
    old_id = await _seed(now - timedelta(days=400))
    fresh_id = await _seed(now - timedelta(minutes=5))
    try:
        async with async_session() as db:
            result = await PostgresAuditBackend().cleanup(
                before=(now - timedelta(days=90)).isoformat(),
                db=SQLAlchemyAuditRepository(db),
            )
        assert result["deleted"] >= 1, result

        # 换一个会话查 = 新连接：查得到说明提交了（旧代码不 commit，删了等于没删）
        from sqlalchemy import select
        from app.models.system_log import SystemLog

        async with async_session() as db:
            left = set((await db.execute(
                select(SystemLog.id).where(SystemLog.id.in_([old_id, fresh_id]))
            )).scalars().all())
        assert old_id not in left, "过期日志必须被删掉"
        assert fresh_id in left, "保留期内的日志不许动"
    finally:
        await _drop(old_id, fresh_id)
