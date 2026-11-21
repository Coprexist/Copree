"""文件写入的两道日常闸（§6.2）：日字节、日新增文件数、每分钟操作次数

为什么单独一个模块：判据是「哪个账户、什么动作」，跟存储层的路径口径无关；计数器是进程
内、按实例时区的日历日重置的（见 utils/rate_limit.DailyCounter）。file_service 只在
「真要写字节」和「附件要进消息」的那一刻问一句还放得下吗，怎么问都在这里。

口径（三条都不重不漏）：
- 日字节：只有真的写了新字节才记。去重命中不写、硬链接收文件不写第二份，所以都不记；
- 日新增文件数：只有新建物理文件才记。同路径覆盖写 inode 没变（physical_key 也不变），
  按 §5.7 的口径它不是一个新文件；
- 每分钟操作次数：上传、发送（附件进消息）、转发各记一次。转发不写字节，但它是传播行为。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import settings
from app.utils.rate_limit import DailyCounter, SlidingWindow

logger = logging.getLogger(__name__)

# 一个实例服务所有人：上限每次调用时按账户类型现取，所以管理页改了立即生效，
# 已有的计数也不会因为换了上限就被清掉。
_OPS = SlidingWindow(limit=1, window=60)     # 窗口就是「一分钟」，上限每次调用时现取
_DAILY_BYTES = DailyCounter()
_DAILY_FILES = DailyCounter()
_NOTIFIED = DailyCounter()      # 告警节流：每类每天只发一条（全局，不分账户）


class FileBudgetExceeded(RuntimeError):
    """额度用完或操作太快：调用方（HTTP 层）翻成 429 + Retry-After"""

    def __init__(self, kind: str, message: str, *, retry_after: int,
                 limit: int, used: int) -> None:
        super().__init__(message)
        self.kind = kind               # bytes / files / ops
        self.retry_after = int(retry_after)
        self.limit = int(limit)
        self.used = int(used)


def limits_for(owner_type: str) -> tuple[int, int, int]:
    """(每天字节 MB, 每天新增文件数, 每分钟操作次数)；0 = 那道闸不设限"""
    ai = owner_type == "ai"
    return (
        max(int(settings.file_daily_mb_ai if ai else settings.file_daily_mb_human), 0),
        max(int(settings.file_daily_files_ai if ai else settings.file_daily_files_human), 0),
        max(int(settings.file_ops_per_minute_ai if ai else settings.file_ops_per_minute_human), 0),
    )


def _clock() -> tuple[str, int]:
    """(今天的日期键, 到明天 0 点还有几秒) —— 按实例时区算，不是 UTC"""
    tz = ZoneInfo(settings.display_timezone or "UTC")
    now = datetime.now(tz)
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return now.strftime("%Y-%m-%d"), max(1, int((tomorrow - now).total_seconds()))


async def _notify_admin(log_type: str, owner_type: str, owner_id: int,
                        details: dict, *, level: str = "WARNING") -> None:
    """撞闸后写一条系统日志（管理端「日志」页可见，按 log_type 可筛选）

    为什么另开一个 session：额度不够时调用方的事务会回滚（该拒的就不该留下半条消息与
    文件行），日志要是写在同一个事务里就跟着一起没了——而那条恰恰是管理员最该看到的。
    """
    from app.database import async_session
    from app.utils.error_handler import log_error

    try:
        async with async_session() as db:
            await log_error(
                db, log_type, owner_type, int(owner_id),
                details=details, level=level,
            )
            await db.commit()
    except Exception:
        logger.warning("写额度告警日志失败", exc_info=True)


def _announce_once(kind: str) -> bool:
    """每类告警每天只报一条，不分账户。

    撞闸可能一分钟几十次、几十个账户一起撞，日志被它淹掉就没人看了；一天一条足够让管理员
    知道「今天这道闸被撞过」，具体是谁、第几次，看那一条的 operator 与 details。
    """
    day, _ = _clock()
    return _NOTIFIED.charge(day, kind, 1, 1)


async def announce_quota_exceeded(owner_type: str, owner_id: int, *,
                                  used: int, quota: int, incoming: int) -> None:
    """「我的文件」满了（413）：告诉管理员一声，同账户每天一条"""
    if not _announce_once("quota"):
        return
    await _notify_admin("file_quota_exceeded", owner_type, owner_id, {
        "used": int(used), "quota": int(quota), "incoming": int(incoming),
    })


async def announce_cleanup_failed(error: Exception) -> None:
    """清理跑挂了（删字节失败、DB 异常）：管理员要知道盘正在长"""
    if not _announce_once("cleanup"):
        return
    await _notify_admin(
        "file_cleanup_failed", "system", 0,
        {"error": str(error)[:500]}, level="ERROR",
    )


async def charge_file_op(owner_type: str, owner_id: int) -> None:
    """记一次文件操作（上传/发送/转发）；太频繁就抛，整笔事务跟着回滚"""
    _mb, _files, per_minute = limits_for(owner_type)
    if not per_minute:
        return
    wait = _OPS.hit(f"{owner_type}:{int(owner_id)}", per_minute)
    if wait:
        if _announce_once("ops"):
            await _notify_admin("file_budget_exceeded", owner_type, owner_id, {
                "kind": "ops", "limit": per_minute, "retry_after": wait,
            })
        raise FileBudgetExceeded(
            "ops",
            f"操作太频繁：每分钟最多 {per_minute} 次文件操作，{wait} 秒后再试",
            retry_after=wait, limit=per_minute, used=per_minute,
        )


async def charge_file_write(owner_type: str, owner_id: int, size: int, *, new_file: bool) -> None:
    """记一次真写字节；额度不够就抛，字节一个都不写

    两份额度先一起判断、都通过才一起记：否则字节记了、文件数被拒，那笔字节就成了
    「额度已经用掉、文件没落盘」的幽灵记录。
    """
    daily_mb, daily_files, _ops = limits_for(owner_type)
    if not daily_mb and not (new_file and daily_files):
        return
    day, until_reset = _clock()
    key = f"{owner_type}:{int(owner_id)}"
    size = max(int(size), 0)

    byte_limit = daily_mb * 1024 * 1024 if daily_mb else 0
    if byte_limit:
        used_bytes = _DAILY_BYTES.used(day, key)
        if used_bytes + size > byte_limit:
            if _announce_once("bytes"):
                await _notify_admin("file_budget_exceeded", owner_type, owner_id, {
                    "kind": "bytes", "limit": int(byte_limit), "used": int(used_bytes),
                    "incoming": size,
                })
            raise FileBudgetExceeded(
                "bytes",
                f"今天的写入额度用完了：已用 {used_bytes / 1048576:.1f} MB / "
                f"上限 {daily_mb} MB，明天零点重置",
                retry_after=until_reset, limit=byte_limit, used=used_bytes,
            )

    if new_file and daily_files:
        used_files = _DAILY_FILES.used(day, key)
        if used_files + 1 > daily_files:
            if _announce_once("files"):
                await _notify_admin("file_budget_exceeded", owner_type, owner_id, {
                    "kind": "files", "limit": int(daily_files), "used": int(used_files),
                })
            raise FileBudgetExceeded(
                "files",
                f"今天新增文件已达上限 {daily_files} 个，明天零点重置",
                retry_after=until_reset, limit=daily_files, used=used_files,
            )

    if byte_limit:
        _DAILY_BYTES.charge(day, key, size, byte_limit)
    if new_file and daily_files:
        _DAILY_FILES.charge(day, key, 1, daily_files)
