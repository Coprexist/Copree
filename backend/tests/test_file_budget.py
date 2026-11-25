"""文件日常额度（§6.2）：日字节、日新增文件数、每分钟操作次数

三条判据都只认「真的发生了什么」：
- 日字节：去重命中不写字节、硬链接收文件不写第二份，都不占额度；
- 日新增文件数：同路径覆盖写 inode 没变，不算新文件（§5.7 的口径）；
- 每分钟操作次数：上传/发送/转发各记一次，超了整笔事务回滚。
上限按账户类型分（人/AI 各一套），0 表示那道闸不设限。
"""
import os
import tempfile

from sqlalchemy import text

HUMAN, AI_AGENT, AI_USER = 1, 49, 120


async def _seed(db):
    from db_reset import clear

    await clear(db, "file_references", "file_metadata", "users", "agents")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES "
        "(:human, '主人', 'x', 'human'), (:ai, '鲸鱼娘账号', 'x', 'ai')"
    ), {"human": HUMAN, "ai": AI_USER})
    await db.execute(text(
        "INSERT INTO agents (id, owner_id, name, user_id, discoverable) VALUES "
        "(:a, :owner, '鲸鱼娘', :ai_user, true)"
    ), {"a": AI_AGENT, "owner": HUMAN, "ai_user": AI_USER})
    await db.commit()


def _with_data_dir():
    from app.config import settings

    data_dir = tempfile.mkdtemp(prefix="budget-")
    original = settings.data_dir
    settings.data_dir = data_dir
    return data_dir, original


def _set(**limits):
    from app.config import settings

    saved = {k: getattr(settings, k) for k in limits}
    for k, v in limits.items():
        setattr(settings, k, v)
    return saved


def _restore(saved):
    from app.config import settings

    for k, v in saved.items():
        setattr(settings, k, v)


def _clear_counters():
    """计数器是进程内状态：同一轮里别的用例记的量不能漏到这一例"""
    from app.services.content.file_budget import _DAILY_BYTES, _DAILY_FILES, _NOTIFIED, _OPS

    _DAILY_BYTES.clear()
    _DAILY_FILES.clear()
    _NOTIFIED.clear()
    _OPS.clear(f"human:{HUMAN}")
    _OPS.clear(f"ai:{AI_AGENT}")


async def _upload(db, path, name, body, owner_type="human", owner_id=HUMAN, overwrite=False):
    from app.services.content.file_service import upload_file

    return await upload_file(db, path, name, body, "application/octet-stream", owner_type, owner_id,
                             overwrite=overwrite)


async def test_daily_bytes_stop_the_next_write(migrated_db):
    """日字节用完：这一笔当场拒绝，字节一个都不写；别的账户不受影响"""
    from app.database import async_session
    from app.config import settings
    from app.services.content.file_budget import FileBudgetExceeded
    from app.services.content.file_service import user_chat_dir

    data_dir, original = _with_data_dir()
    saved = _set(file_daily_mb_human=1)      # 1 MB
    _clear_counters()
    try:
        async with async_session() as db:
            await _seed(db)
            await _upload(db, user_chat_dir(HUMAN), "a.bin", b"a" * 600_000)
            await db.commit()

            try:
                await _upload(db, user_chat_dir(HUMAN), "b.bin", b"b" * 600_000)
            except FileBudgetExceeded as e:
                assert e.kind == "bytes", e.kind
                assert e.limit == 1024 * 1024, e.limit
                assert e.used == 600_000, e.used
                assert e.retry_after > 0, e.retry_after
            else:
                raise AssertionError("超过日字节上限必须拒绝")

            assert not os.path.exists(os.path.join(data_dir, "users/1/chat/b.bin")), \
                "被拒的那笔不该留下字节"

            # 另一个占用者（AI）有自己的额度：人这边空了，不影响它
            await _upload(db, f"agents/{AI_AGENT}/inbox", "c.bin", b"c" * 600_000,
                          owner_type="ai", owner_id=AI_AGENT)
            await db.commit()
    finally:
        _restore(saved)
        settings.data_dir = original


async def test_dedup_hit_does_not_spend_the_daily_budget(migrated_db):
    """同一个文件再传一次不写字节：额度用完了也放行"""
    from app.database import async_session
    from app.config import settings
    from app.services.content.file_service import user_chat_dir

    data_dir, original = _with_data_dir()
    saved = _set(file_daily_mb_human=1, file_daily_files_human=1)
    _clear_counters()
    try:
        async with async_session() as db:
            await _seed(db)
            body = b"z" * 600_000
            await _upload(db, user_chat_dir(HUMAN), "same.bin", body)
            await db.commit()
            again = await _upload(db, user_chat_dir(HUMAN), "same.bin", body)
            await db.commit()
        assert again is not None
    finally:
        _restore(saved)
        settings.data_dir = original


async def test_overwriting_the_same_path_is_not_a_new_file(migrated_db):
    """同路径覆盖写：字节变了但 inode 没变，不算「新增一个文件」"""
    from app.database import async_session
    from app.config import settings
    from app.services.content.file_service import user_chat_dir

    data_dir, original = _with_data_dir()
    saved = _set(file_daily_files_human=1, file_daily_mb_human=0)
    _clear_counters()
    try:
        async with async_session() as db:
            await _seed(db)
            await _upload(db, user_chat_dir(HUMAN), "one.bin", b"x" * 1000)
            await db.commit()
            # 同一路径、内容不同——**显式要求覆盖**才走这条（默认会换名，见 _next_free_path）
            await _upload(db, user_chat_dir(HUMAN), "one.bin", b"y" * 2000, overwrite=True)
            await db.commit()
    finally:
        _restore(saved)
        settings.data_dir = original


async def test_daily_file_count_limits_new_files(migrated_db):
    """日新增文件数：第二个不同名的文件被拒"""
    from app.database import async_session
    from app.config import settings
    from app.services.content.file_budget import FileBudgetExceeded
    from app.services.content.file_service import user_chat_dir

    data_dir, original = _with_data_dir()
    saved = _set(file_daily_files_human=1, file_daily_mb_human=0)
    _clear_counters()
    try:
        async with async_session() as db:
            await _seed(db)
            await _upload(db, user_chat_dir(HUMAN), "one.bin", b"x" * 10)
            await db.commit()
            try:
                await _upload(db, user_chat_dir(HUMAN), "two.bin", b"y" * 10)
            except FileBudgetExceeded as e:
                assert e.kind == "files", e.kind
            else:
                raise AssertionError("超过日新增文件数必须拒绝")
    finally:
        _restore(saved)
        settings.data_dir = original


async def test_keeping_a_file_does_not_spend_daily_bytes(migrated_db):
    """收进我的文件是硬链接：盘上不多一份字节，日字节额度也不该被记"""
    from app.database import async_session
    from app.config import settings
    from app.services.content.file_budget import _DAILY_BYTES
    from app.services.content.file_service import keep_to_my_files

    data_dir, original = _with_data_dir()
    saved = _set(file_daily_mb_human=1)
    _clear_counters()
    try:
        async with async_session() as db:
            await _seed(db)
            rel = "users/1/chat/big.bin"
            path = os.path.join(data_dir, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(b"q" * 2_000_000)          # 2 MB：单看字节就超过 1 MB 的日额度
            fid = (await db.execute(text(
                "INSERT INTO file_metadata (path, owner_type, owner_id, size, mime_type,"
                " collaboration_mode) VALUES (:p, 'human', :u, 2000000, 'application/octet-stream', 'solo')"
                " RETURNING id"
            ), {"p": rel, "u": HUMAN})).scalar_one()
            await db.commit()

            kept = await keep_to_my_files(db, HUMAN, fid)
            await db.commit()

            day_used = _DAILY_BYTES.used(
                __import__("datetime").datetime.now(
                    __import__("zoneinfo").ZoneInfo(settings.display_timezone)
                ).strftime("%Y-%m-%d"),
                f"human:{HUMAN}",
            )
        assert kept["already"] is False
        assert day_used == 0, f"硬链接不该记日字节（记了 {day_used}）"
    finally:
        _restore(saved)
        settings.data_dir = original


async def test_per_minute_ops_gate(migrated_db):
    """每分钟操作次数：第三次上传被拒，另一个占用者不受影响"""
    from app.database import async_session
    from app.config import settings
    from app.services.content.file_budget import FileBudgetExceeded
    from app.services.content.file_service import user_chat_dir

    data_dir, original = _with_data_dir()
    saved = _set(file_ops_per_minute_human=2, file_daily_mb_human=0, file_daily_files_human=0)
    _clear_counters()
    try:
        async with async_session() as db:
            await _seed(db)
            await _upload(db, user_chat_dir(HUMAN), "a.bin", b"a")
            await _upload(db, user_chat_dir(HUMAN), "b.bin", b"b")
            try:
                await _upload(db, user_chat_dir(HUMAN), "c.bin", b"c")
            except FileBudgetExceeded as e:
                assert e.kind == "ops", e.kind
                assert 0 < e.retry_after <= 60, e.retry_after
            else:
                raise AssertionError("超过每分钟操作次数必须拒绝")

            await _upload(db, f"agents/{AI_AGENT}/inbox", "d.bin", b"d",
                          owner_type="ai", owner_id=AI_AGENT)
            await db.commit()
    finally:
        _restore(saved)
        settings.data_dir = original


async def test_zero_means_no_limit(migrated_db):
    """0 表示这道闸不设限（不是"一个很小的额度"）"""
    from app.database import async_session
    from app.config import settings
    from app.services.content.file_service import user_chat_dir

    data_dir, original = _with_data_dir()
    saved = _set(file_ops_per_minute_human=0, file_daily_mb_human=0, file_daily_files_human=0)
    _clear_counters()
    try:
        async with async_session() as db:
            await _seed(db)
            for i in range(5):
                await _upload(db, user_chat_dir(HUMAN), f"f{i}.bin", bytes([i]) * 10)
            await db.commit()
    finally:
        _restore(saved)
        settings.data_dir = original


async def test_a_block_reports_one_system_log_per_day(migrated_db):
    """撞闸要给管理员留一条系统日志；同类当天只报一次，别把日志刷满"""
    from app.database import async_session
    from app.config import settings
    from app.services.content.file_budget import FileBudgetExceeded
    from app.services.content.file_service import user_chat_dir

    data_dir, original = _with_data_dir()
    saved = _set(file_daily_mb_human=1)
    _clear_counters()
    try:
        async with async_session() as db:
            await _seed(db)
            await db.execute(text("DELETE FROM system_logs"))
            await db.commit()
            await _upload(db, user_chat_dir(HUMAN), "a.bin", b"a" * 600_000)
            for name in ("b.bin", "c.bin"):
                try:
                    await _upload(db, user_chat_dir(HUMAN), name, b"b" * 600_000)
                except FileBudgetExceeded:
                    pass
            await db.commit()
            logged = (await db.execute(text(
                "SELECT count(*) FROM system_logs WHERE log_type = 'file_budget_exceeded'"
            ))).scalar_one()
            assert logged == 1, f"同一天同类撞闸只该报一条，实际 {logged}"
    finally:
        _restore(saved)
        settings.data_dir = original


async def test_a_block_is_reported_once_a_day_across_accounts(migrated_db):
    """每类告警一天只报一条：另一个账户撞同一道闸，不再写第二条"""
    from app.database import async_session
    from app.config import settings
    from app.services.content.file_budget import FileBudgetExceeded
    from app.services.content.file_service import user_chat_dir

    data_dir, original = _with_data_dir()
    saved = _set(file_daily_mb_human=1, file_daily_mb_ai=1)
    _clear_counters()
    try:
        async with async_session() as db:
            await _seed(db)
            await db.execute(text("DELETE FROM system_logs"))
            await db.commit()

            await _upload(db, user_chat_dir(HUMAN), "a.bin", b"a" * 600_000)
            try:
                await _upload(db, user_chat_dir(HUMAN), "b.bin", b"b" * 600_000)
            except FileBudgetExceeded:
                pass
            await db.commit()

            await _upload(db, f"agents/{AI_AGENT}/inbox", "c.bin", b"c" * 600_000,
                          owner_type="ai", owner_id=AI_AGENT)
            try:
                await _upload(db, f"agents/{AI_AGENT}/inbox", "d.bin", b"d" * 600_000,
                              owner_type="ai", owner_id=AI_AGENT)
            except FileBudgetExceeded:
                pass
            await db.commit()

            logged = (await db.execute(text(
                "SELECT count(*) FROM system_logs WHERE log_type = 'file_budget_exceeded'"
            ))).scalar_one()
            assert logged == 1, f"两个账户撞同一道闸，当天也只该报一条，实际 {logged}"
    finally:
        _restore(saved)
        settings.data_dir = original
