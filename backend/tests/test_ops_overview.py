"""运维总览：每个数字的口径（真库聚合，不假数据）。

口径写在 admin.ops_overview 的 docstring 里，这里把「说好的口径」钉成会红的用例：
窗口外的行不算、登录失败与被锁定挡回不重复计数、疑似攻击来源只认到门槛的。
"""
import pytest
from sqlalchemy import text

pytestmark = pytest.mark.anyio


async def _seed(db):
    """3 次成功登录、6 次真失败（其中 1 次当场锁定）、2 次被锁挡回、1 次注册，外加 1 条窗口外的旧登录。

    先清空审计表：migrated_db 是 session 级、用例之间不清库，而总览是按窗口数**全表**的，
    别让别的用例写的行串到这里的断言上（本文件只用 system_logs 做种子）。
    """
    await db.execute(text("DELETE FROM system_logs"))
    async def log(log_type, *, operator_id=1, error="", ip=None, details=None, days_ago=0):
        await db.execute(text("""
            INSERT INTO system_logs (log_type, operator_type, operator_id, target_type, success,
                                     error_message, ip_address, details, hash, created_at)
            VALUES (:t, 'human', :oid, 'user', :ok, :err, :ip, CAST(:d AS jsonb), 'test', now() - make_interval(days => :ago))
        """), {"t": log_type, "oid": operator_id, "ok": log_type != "login_failed",
               "err": error, "ip": ip, "d": details or "{}", "ago": days_ago})

    for uid in (1, 2, 3):
        await log("login", operator_id=uid)
    await log("login", operator_id=4, days_ago=20)                     # 7 天窗口外、30 天窗口内
    for _ in range(5):
        await log("login_failed", operator_id=0, error="用户名或密码错误", ip="10.0.0.1",
                  details='{"login_id": "acct-a", "kind": "fail"}')
    await log("login_failed", operator_id=0, error="用户名或密码错误", ip="10.0.0.1",
              details='{"login_id": "acct-a", "kind": "fail", "locked_for": 300}')   # 打满阈值当场锁
    for _ in range(2):
        await log("login_failed", operator_id=0, error="账号或来源已被锁定", ip="10.0.0.1",
                  details='{"login_id": "acct-a", "kind": "locked", "locked_for": 300}')
    await log("register", operator_id=9)
    await db.commit()


async def test_ops_overview_counts_within_the_window_only(migrated_db):
    from app.database import async_session
    from app.routers import admin as admin_router

    async with async_session() as db:
        await _seed(db)
        out = await admin_router.ops_overview(days=7, admin={"user_id": 1}, db=db)

    assert out["login"]["success"] == 3, out          # 30 天前那条不算
    assert out["login"]["success_accounts"] == 3, out
    assert out["login"]["failed"] == 6, out           # 5 次失败 + 1 次打满阈值；不含被锁挡回的 2 次
    assert out["login"]["failed_accounts"] == 1, out
    assert out["lockouts"] == 1, out                  # 失败打满阈值 → 锁定
    assert out["blocked"]["lockout"] == 2, out        # 被锁定挡回去的尝试
    assert out["blocked"]["total"] >= out["blocked"]["lockout"], out   # 限流那半是进程内计数
    assert out["attack"]["suspects"] == 2, out        # 来源 IP + 被撞账号各算一个
    assert out["attack"]["top"]["failures"] == 6, out
    # 诚实口径：反复失败占比（同一 IP 的 6 次都算"回头客"），以及限流计数是否只覆盖部分窗口
    assert out["attack"]["repeat_failures"] == 6 and out["attack"]["total_failures"] == 6, out
    assert out["blocked"]["throttle_partial"] is True, out   # 进程刚起，覆盖不了 7 天窗口
    assert out["people"]["new_audit"] == 1, out


async def test_ops_overview_window_widens_to_include_old_rows(migrated_db):
    """窗口是可调的：30 天口径要把 7 天看不上的那条（20 天前）补回来"""
    from app.database import async_session
    from app.routers import admin as admin_router

    async with async_session() as db:
        await _seed(db)
        out = await admin_router.ops_overview(days=30, admin={"user_id": 1}, db=db)

    assert out["login"]["success"] == 4, out
    assert out["days"] == 30 and out["since"], out


async def test_ops_overview_suspect_threshold(migrated_db):
    """不到门槛（5 次）的来源不算疑似攻击：一两次是手滑"""
    from app.database import async_session
    from app.routers import admin as admin_router

    async with async_session() as db:
        await db.execute(text("DELETE FROM system_logs"))
        await db.execute(text("""
            INSERT INTO system_logs (log_type, operator_type, operator_id, target_type, success,
                                     error_message, ip_address, details, hash)
            VALUES ('login_failed', 'human', 0, 'user', false, '用户名或密码错误', '10.9.9.9',
                    CAST('{"login_id": "acct-b", "kind": "fail"}' AS jsonb), 'test')
        """))
        await db.commit()
        out = await admin_router.ops_overview(days=1, admin={"user_id": 1}, db=db)

    assert out["attack"]["suspects"] == 0, out
    assert out["attack"]["top"] is None, out
