"""登录失败计数、锁定与失败审计。

为什么放在进程内：这两个计数器要求「低延迟、无副作用」，为它们引入 Redis 或新表不值当；
当前部署是 uvicorn 单进程，进程内状态全站可见。重启即清空是有意的取舍——
重启后攻击者也从头计数，而运维多了一条不解封就能救急的路（见 utils/rate_limit.py）。

键分两种：**账号**（撞库试同一个账号的很多密码）与**来源 IP**（一个来源试很多账号）。
IP 阈值必须比账号高：docker 端口映射下后端看到的源永远是网关地址（middleware.py 顶部
有说明，审计里的 IP 实测也是它），15 次就锁 IP 等于任何人失败十几次就能把**全站**登录
锁五分钟——那比撞库更容易得手。

失败审计**不跟 audit_user_actions 开关走**：那个开关管的是「用户行为日志」，
失败登录是安全事件；挡在开关后面，撞库在日志里就是隐形的，正是要修的问题。
审计写入失败只告警，不让它把 401 变成 500。
"""
from __future__ import annotations

import logging

from app.config import settings
from app.utils.rate_limit import FailureGuard, SlidingWindow

logger = logging.getLogger(__name__)

_accounts = FailureGuard(
    settings.auth_fail_threshold, settings.auth_lock_seconds,
    window=settings.auth_fail_window, max_lock_seconds=settings.auth_lock_max_seconds,
)
_sources = FailureGuard(
    settings.auth_fail_ip_threshold, settings.auth_lock_seconds,
    window=settings.auth_fail_window, max_lock_seconds=settings.auth_lock_max_seconds,
)
# 锁定期内还接着打的话，每次尝试都落一条审计会把审计表刷爆（哈希链是逐条读上一次的 hash）。
# 同一账号每分钟最多留一条「还在被锁着打」的记录，够看出趋势又不至于变成写放大入口。
_locked_audit = SlidingWindow(1, 60, max_keys=1024)


def _account_key(login_id: str) -> str:
    """账号键归一（去空白 + 小写）：大小写换个写法不该白送攻击者一份新配额"""
    return f"user:{str(login_id or '').strip().lower()}"


def retry_after(login_id: str, ip: str | None) -> int:
    """这个账号或来源还在锁定期内吗：返回需要等待的秒数（0 = 没锁）"""
    locked = _accounts.retry_after(_account_key(login_id))
    if ip:
        locked = max(locked, _sources.retry_after(f"ip:{ip}"))
    return locked


def count_failure(login_id: str, ip: str | None) -> int:
    """记一次失败：返回锁定剩余秒数（0 = 还没到阈值，>0 = 刚被锁）"""
    locked = _accounts.record(_account_key(login_id))
    if ip:
        # 成功登录不清来源计数：否则攻击者只要手里有一个能登上去的账号，就能顺手洗掉它
        locked = max(locked, _sources.record(f"ip:{ip}"))
    return locked


def clear(login_id: str) -> None:
    """登录成功：清掉这个账号的失败计数（来源计数留着，靠窗口自然过期）"""
    _accounts.clear(_account_key(login_id))


async def audit_failure(*, login_id: str, ip: str | None, reason: str,
                        locked_for: int = 0, repeat: bool = False) -> None:
    """写一条失败登录审计（含被锁定当场拒掉的那次）。

    用**自己的会话**并当场提交：请求那条会话在 get_db 里是「有异常就回滚」，
    而失败登录必然以 401/429 收场——挂在请求会话上写的审计会被一起回滚掉，
    等于没写（这正是「撞库在日志里隐形」的成因）。

    operator_id=0 表示「没通过认证的匿名尝试」：审计表要的是操作者，失败登录没有操作者
    （operator_type 保持 human，那一列的注释只认 human|ai|system）。
    """
    if repeat and _locked_audit.hit(_account_key(login_id)) > 0:
        return
    # kind 是给运维统计看的（而不是给人读文案）：fail = 真的输错了密码，locked = 被锁定挡回去
    details: dict = {"login_id": login_id, "kind": "locked" if repeat else "fail"}
    if locked_for:
        details["locked_for"] = locked_for
    try:
        from app.database import async_session
        from app.repositories.audit_repo import SQLAlchemyAuditRepository
        from app.services.audit_service import create_audit_log

        async with async_session() as session:
            await create_audit_log(
                SQLAlchemyAuditRepository(session),
                log_type="login_failed",
                operator_type="human",
                operator_id=0,
                target_type="user",
                success=False,
                error_message=reason,
                ip_address=ip,
                details=details,
            )
            await session.commit()
    except Exception as e:
        logger.warning("失败登录审计写入失败：%s: %s", type(e).__name__, e)
