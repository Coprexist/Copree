"""登录防爆破：失败计数/锁定/请求限流/失败审计。

覆盖三件线上会踩的事：
1. 连续失败要锁定，而且锁定期间不再验密码（这里锁的是计数与状态机）；
2. /auth/* 的请求限流分两档，凭证端点严格、其余宽松；
3. **失败登录必须留痕**——请求会话遇异常会回滚（get_db），所以审计要自己开会话提交，
   否则撞库在日志里仍然是隐形的（这条用真库真接口验）。
"""
import time

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.anyio


# ── 原语 ──────────────────────────────────────────────

def test_sliding_window_allows_then_blocks_and_recovers():
    from app.utils.rate_limit import SlidingWindow

    window = SlidingWindow(limit=3, window=60)
    assert [window.hit("ip:1") for _ in range(3)] == [0, 0, 0]
    blocked = window.hit("ip:1")
    assert blocked > 0, blocked                      # 超限：告诉调用方等几秒
    assert window.hit("ip:2") == 0, "别的 key 不该被连坐"
    window.clear("ip:1")
    assert window.hit("ip:1") == 0


def test_sliding_window_forgets_hits_outside_the_window():
    from app.utils.rate_limit import SlidingWindow

    window = SlidingWindow(limit=1, window=0.05)
    assert window.hit("k") == 0
    assert window.hit("k") > 0
    time.sleep(0.08)
    assert window.hit("k") == 0, "窗口外的失败不该继续算数"


def test_primitive_keys_are_bounded():
    """键来自外部输入（用户名/IP）：不设上界等于给攻击者一个塞爆进程的口子"""
    from app.utils.rate_limit import FailureGuard, SlidingWindow

    window = SlidingWindow(limit=1, window=60, max_keys=2)
    for i in range(5):
        window.hit(f"k{i}")
    assert len(window) <= 2, len(window)

    guard = FailureGuard(threshold=3, lock_seconds=60, max_keys=2)
    for i in range(5):
        guard.record(f"k{i}")
    assert len(guard) <= 2, len(guard)


def test_failure_guard_locks_then_backs_off_exponentially():
    from app.utils.rate_limit import FailureGuard

    guard = FailureGuard(threshold=2, lock_seconds=0.05, window=60, max_lock_seconds=0.2)
    assert guard.record("acct") == 0                 # 第 1 次：还没到阈值
    assert guard.record("acct") > 0                  # 第 2 次：当场锁上
    assert guard.retry_after("acct") > 0
    assert abs(guard.next_lock_seconds("acct") - 0.1) < 1e-9, "解锁后再犯要翻倍"

    time.sleep(0.08)
    assert guard.retry_after("acct") == 0
    guard.record("acct")
    guard.record("acct")
    assert abs(guard.next_lock_seconds("acct") - 0.2) < 1e-9
    time.sleep(0.25)
    guard.record("acct")
    guard.record("acct")
    assert abs(guard.next_lock_seconds("acct") - 0.2) < 1e-9, "翻倍有上限"

    guard.clear("acct")
    assert guard.retry_after("acct") == 0
    assert abs(guard.next_lock_seconds("acct") - 0.05) < 1e-9, "清零后回到基础时长"


# ── 策略：账号 / 来源两个口径 ──────────────────────────

def test_account_locks_at_threshold_and_success_clears_it():
    from app.config import settings
    from app.services.infrastructure import login_guard

    account, ip = "guard-test-account", "198.51.100.11"
    login_guard.clear(account)
    locked = 0
    for _ in range(settings.auth_fail_threshold):
        locked = login_guard.count_failure(account, ip)
    assert locked > 0, "到了阈值就该锁"
    assert login_guard.retry_after(account, None) > 0
    # 大小写/空白换个写法不该白送一份新配额
    assert login_guard.retry_after(f"  {account.upper()}  ", None) > 0
    login_guard.clear(account)
    assert login_guard.retry_after(account, None) == 0


def test_source_ip_locks_at_its_own_threshold_and_success_does_not_clear_it():
    """一个来源试很多账号也要拦；但成功登录不清来源计数（否则一个有效账号就能洗掉）"""
    from app.config import settings
    from app.services.infrastructure import login_guard

    ip = "198.51.100.12"
    locked = 0
    for i in range(settings.auth_fail_ip_threshold):
        locked = login_guard.count_failure(f"spray-{i}", ip)
    assert locked > 0, "同来源刷很多账号也要被锁"
    assert login_guard.retry_after("任何人", ip) > 0
    assert login_guard.retry_after("任何人", None) == 0, "不能连累没被刷的账号"
    login_guard.clear("spray-0")
    assert login_guard.retry_after("任何人", ip) > 0, "账号清零不该动来源计数"


# ── 失败审计：必须能落库（且不被请求会话的回滚吃掉）──────

async def test_failed_login_audit_is_committed_on_its_own_session(migrated_db):
    from app.database import async_session
    from app.services.infrastructure import login_guard

    await login_guard.audit_failure(login_id="eve", ip="203.0.113.44",
                                    reason="用户名或密码错误", locked_for=300)
    async with async_session() as db:
        row = (await db.execute(text(
            "SELECT log_type, operator_type, operator_id, target_type, success, error_message, "
            "ip_address, details FROM system_logs WHERE log_type = 'login_failed' "
            "ORDER BY id DESC LIMIT 1"))).first()
    assert row is not None, "失败登录必须在审计里留痕"
    assert row[0] == "login_failed" and row[1] == "human" and row[2] == 0, row
    assert row[4] is False and row[5] == "用户名或密码错误", row
    assert row[6] == "203.0.113.44" and row[7]["login_id"] == "eve", row
    assert row[7]["locked_for"] == 300, row


# ── 中间件：/auth/* 分两档 ────────────────────────────

def _request(path: str, ip: str):
    from starlette.requests import Request

    return Request({
        "type": "http", "http_version": "1.1", "method": "POST", "scheme": "http",
        "path": path, "raw_path": path.encode(), "query_string": b"", "headers": [],
        "client": (ip, 45678), "server": ("testserver", 80),
    })


async def _ok(request):
    from starlette.responses import Response

    return Response("ok")


async def test_auth_middleware_throttles_credential_endpoints():
    from app.config import settings
    from app.middleware import auth_rate_limit_middleware

    ip = "2030:db8::1"                               # 每个用例一个 IP，避免互相影响
    codes = []
    for _ in range(settings.auth_rate_limit + 1):
        resp = await auth_rate_limit_middleware(_request("/auth/login", ip), _ok)
        codes.append(resp.status_code)
    assert codes[:-1] == [200] * settings.auth_rate_limit, codes
    assert codes[-1] == 429, codes
    resp = await auth_rate_limit_middleware(_request("/auth/login", ip), _ok)
    assert resp.headers.get("Retry-After"), resp.headers


async def test_credential_buckets_are_per_path():
    """登录被限流不该把注册也一起挡掉"""
    from app.config import settings
    from app.middleware import auth_rate_limit_middleware

    ip = "2030:db8::2"
    for _ in range(settings.auth_rate_limit):
        await auth_rate_limit_middleware(_request("/auth/login", ip), _ok)
    assert (await auth_rate_limit_middleware(_request("/auth/login", ip), _ok)).status_code == 429
    assert (await auth_rate_limit_middleware(_request("/auth/register", ip), _ok)).status_code == 200


async def test_auth_middleware_is_loose_for_non_credential_paths_and_ignores_other_paths():
    from app.config import settings
    from app.middleware import auth_rate_limit_middleware

    ip = "2030:db8::3"
    # /auth/me 每次页面加载都会调，配额不能和凭证端点一个数
    for _ in range(settings.auth_rate_limit + 1):
        assert (await auth_rate_limit_middleware(_request("/auth/me", ip), _ok)).status_code == 200
    for _ in range(10):
        assert (await auth_rate_limit_middleware(_request("/groups", ip), _ok)).status_code == 200


# ── 真接口：连打错密码 ────────────────────────────────

async def test_login_endpoint_locks_the_account_and_audits_every_failure(migrated_db):
    import httpx

    from app.config import settings
    from app.database import async_session
    from app.main import app
    from app.services.infrastructure import login_guard

    account = "bruteforce-target"
    login_guard.clear(account)
    body = {"login_id": account, "password": "wrong-password", "method": "direct"}
    statuses = []
    # 每个用例一个源 IP：中间件的凭证配额是按来源算的，共用 127.0.0.1 会互相把配额吃光
    transport = httpx.ASGITransport(app=app, client=("198.18.0.10", 123))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for _ in range(settings.auth_fail_threshold):
            statuses.append((await client.post("/auth/login", json=body)).status_code)
        assert statuses[:-1] == [401] * (settings.auth_fail_threshold - 1), statuses
        assert statuses[-1] == 429, f"第 {settings.auth_fail_threshold} 次失败当场锁定：{statuses}"
        blocked = await client.post("/auth/login", json=body)
        assert blocked.status_code == 429 and blocked.headers.get("Retry-After"), blocked.headers
        for _ in range(3):                       # 锁定期内接着打：审计不再逐条写（写放大）
            assert (await client.post("/auth/login", json=body)).status_code == 429

    # 每一次失败都留了痕，包括被锁定当场拒掉的那次
    async with async_session() as db:
        rows = (await db.execute(text(
            "SELECT success, operator_id, error_message, details FROM system_logs "
            "WHERE log_type = 'login_failed' AND details->>'login_id' = :a ORDER BY id"),
            {"a": account})).all()
    assert len(rows) == settings.auth_fail_threshold + 1, len(rows)
    assert all(r[0] is False and r[1] == 0 for r in rows), rows
    assert rows[-1][3].get("locked_for"), rows[-1]

async def test_successful_login_clears_the_account_counter_and_is_audited(migrated_db):
    """成功一次即清零：不然后面几次手滑就凑够阈值把正常用户锁掉"""
    import httpx

    from app.database import async_session
    from app.main import app
    from app.services.infrastructure import login_guard
    from app.utils.auth import hash_password

    account = "guard-success-target"
    login_guard.clear(account)
    async with async_session() as db:
        # 显式给 is_active/role：ORM 的 default 不会作用在裸 SQL 上，缺了会被当成"已封禁"
        await db.execute(text(
            "INSERT INTO users (username, password_hash, type, role, is_active, "
            "setup_completed, email_verified) "
            "VALUES (:u, :p, 'human', 'user', true, true, false)"),
            {"u": account, "p": hash_password("right-password")})
        await db.commit()

    wrong = {"login_id": account, "password": "wrong-password", "method": "direct"}
    right = {"login_id": account, "password": "right-password", "method": "direct"}
    transport = httpx.ASGITransport(app=app, client=("198.18.0.11", 123))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for _ in range(5):
            assert (await client.post("/auth/login", json=wrong)).status_code == 401
        ok = await client.post("/auth/login", json=right)
        assert ok.status_code == 200 and ok.json().get("access_token"), ok.text
        # 失败计数已清零：再失败 14 次也够不到阈值（没清零的话第 10 次就该锁了）
        for _ in range(14):
            assert (await client.post("/auth/login", json=wrong)).status_code == 401
        assert (await client.post("/auth/login", json=wrong)).status_code == 429, "清过的计数要能重新累计"

    async with async_session() as db:
        row = (await db.execute(text(
            "SELECT success, details FROM system_logs WHERE log_type = 'login' "
            "AND operator_id = (SELECT id FROM users WHERE username = :u) ORDER BY id DESC LIMIT 1"),
            {"u": account})).first()
    assert row is not None and row[0] is True, "成功登录仍要留审计"


async def test_throttled_requests_are_counted_for_the_ops_page():
    """限流挡回的请求要计数：运维总览的「已拦截」有一半来自它（进程内，重启归零）"""
    from app.config import settings
    from app.middleware import auth_limit_stats, auth_rate_limit_middleware

    before = auth_limit_stats()["total"]
    ip = "2030:db8::9"
    for _ in range(settings.auth_rate_limit + 1):
        await auth_rate_limit_middleware(_request("/auth/login", ip), _ok)
    after = auth_limit_stats()
    assert after["total"] == before + 1, after            # 放行的那些不算，只记被拒的那一次
    assert after["by_path"].get("/auth/login"), after
