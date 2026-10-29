"""
HTTP 中间件 — 请求日志/Request ID + CORS + 请求 IP 追踪 + 维护模式拦截。

IP 追踪分两种口径，统一由 resolve_client_ip() 决定：
- 默认（管理员未开启「记录真实客户端 IP」）：只取 TCP 对端地址，不采信任何转发头；
- 开启且对端落在 TRUSTED_PROXY_CIDRS 内：采用边缘反向代理写入的 X-Real-IP
  （nginx 用 $remote_addr 覆盖，客户端自带的值无效）。
审计入库的 IP 与 /auth 限流的分桶键都走这一个入口，避免两处口径漂移。
"""
import ipaddress
import logging
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import settings
from app.database import async_session
from app.services.infrastructure.maintenance import maintenance
from app.utils.auth import set_current_request_ip
from app.utils.rate_limit import SlidingWindow

logger = logging.getLogger(__name__)


# ── 真实客户端 IP 解析 ──

def _parse_net(cidr: str):
    try:
        return ipaddress.ip_network(cidr.strip(), strict=False)
    except ValueError:
        return None


_TRUSTED_PROXY_NETS = [
    net for net in (_parse_net(c) for c in (settings.trusted_proxy_cidrs or "").split(",")) if net
]

# 开关读库结果的 TTL 缓存：设置页改完最多 30s 生效，避免每请求查一次库
_AUDIT_REAL_IP_TTL = 30.0
_audit_real_ip_cache: dict = {"ts": 0.0, "value": False}
_UNSET = object()


def _parse_ip(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        return None


def _is_trusted_proxy(peer: str | None) -> bool:
    if not peer or not _TRUSTED_PROXY_NETS:
        return False
    try:
        addr = ipaddress.ip_address(peer)
    except ValueError:
        return False
    return any(addr in net for net in _TRUSTED_PROXY_NETS)


async def _audit_use_real_ip() -> bool:
    now = time.monotonic()
    if now - _audit_real_ip_cache["ts"] < _AUDIT_REAL_IP_TTL:
        return _audit_real_ip_cache["value"]
    try:
        async with async_session() as db:
            value = (await db.execute(
                text("SELECT audit_use_real_ip FROM system_settings WHERE id = 1")
            )).scalar()
        value = bool(value)
    except Exception:
        # 数据库未就绪/表不存在时按关闭处理：不影响请求本身
        value = False
    _audit_real_ip_cache.update(ts=now, value=value)
    return value


async def resolve_client_ip(request: Request) -> str | None:
    """解析客户端 IP：默认 TCP 对端；开关打开且对端是可信代理时取 X-Real-IP。"""
    cached = getattr(request.state, "resolved_client_ip", _UNSET)
    if cached is not _UNSET:
        return cached

    peer = request.client.host if request.client else None
    result = peer
    if peer and await _audit_use_real_ip() and _is_trusted_proxy(peer):
        real = _parse_ip(request.headers.get("x-real-ip"))
        if real:
            result = real
        else:
            # 兼容只传 X-Forwarded-For 的反代：取最右一跳（由我们的边缘代理追加）
            forwarded = request.headers.get("x-forwarded-for")
            if forwarded:
                for part in reversed(forwarded.split(",")):
                    ip = _parse_ip(part)
                    if ip:
                        result = ip
                        break
    request.state.resolved_client_ip = result
    return result

# 硬维护放行路径：健康检查/文档/管理/认证/维护消息本身/维护弹窗公开图片
_EXACT_BYPASS = ("/health", "/", "/docs", "/openapi.json")
_PREFIX_BYPASS = ("/admin", "/auth", "/maintenance-msg", "/fs/public")


async def request_logging_middleware(request: Request, call_next):
    """为每个请求生成/透传 Request ID，记录请求耗时"""
    # 优先使用客户端透传的 X-Request-ID，否则生成完整 UUID
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    # 存储到 state，供全局异常处理器关联日志
    request.state.request_id = request_id
    start = time.monotonic()
    response = await call_next(request)
    elapsed_ms = (time.monotonic() - start) * 1000
    response.headers["X-Request-ID"] = request_id
    # 安全响应头（只加零风险的两项）：
    #   nosniff   —— 禁止浏览器按内容嗅探类型（上传文件/世界文件防被当脚本执行）
    #   Referrer-Policy —— 外链（联邦、世界里的第三方资源）不携带完整路径
    # 刻意不加：X-Frame-Options（世界页面允许被其它实例 iframe 嵌入）、
    # CSP（世界 HTML 依赖内联脚本，需先做 nonce 体系）、HSTS（后端只见 http，应在 TLS 终结层加）
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    if request.url.path != "/health":
        client_ip = await resolve_client_ip(request)
        logger.info(
            f"[{request_id}] {client_ip or '-'} {request.method} {request.url.path} → "
            f"{response.status_code} ({elapsed_ms:.0f}ms)"
        )
    return response


async def client_ip_middleware(request: Request, call_next):
    """记录请求 IP 到 contextvar（供审计日志使用）"""
    try:
        set_current_request_ip(await resolve_client_ip(request))
    except Exception as e:
        logger.exception(f"client_ip_middleware error: {e}")
    return await call_next(request)


# 凭证端点：拿密码/验证码换令牌的那些，单独用严格配额。
# 其余 /auth/*（/auth/me、/auth/has-users、/auth/login-providers…）每次页面加载都会调，
# 用同一个配额会把正常刷新也挡掉——所以分两档，而不是给整个前缀一个数。
_AUTH_CREDENTIAL_PATHS = (
    "/auth/login", "/auth/register", "/auth/send-verification-code", "/auth/verify-email",
)
# 窗口固定一分钟：配额就是「每分钟多少次」，改窗口会让配置项的含义跟着变
_AUTH_WINDOW = 60.0
_auth_credential_window = SlidingWindow(settings.auth_rate_limit, _AUTH_WINDOW)
_auth_other_window = SlidingWindow(settings.auth_rate_limit_other, _AUTH_WINDOW)

# 限流拦截计数：运维总览要能直接报「拦了多少」，而 429 不落库——每条都写审计会变成写放大入口。
# 只记 /auth 的两档（路径有限，键不会膨胀），按 UTC 天分桶、保留 30 天；进程内计数，重启归零，
# 页面照实这么写（这也是它与「锁定拦截」不同口径的原因，后者来自审计、是持久的）。
_BLOCKED_KEEP_DAYS = 30
_started_at = time.time()          # 进程启动时刻（UTC 秒）：限流计数只覆盖这之后，页面要照实标
_blocked_total = 0
_blocked_by_day: dict[str, int] = {}
_blocked_by_path: dict[str, int] = {}


def _count_blocked(path: str) -> None:
    global _blocked_total
    day = time.strftime("%Y-%m-%d", time.gmtime())
    _blocked_total += 1
    _blocked_by_day[day] = _blocked_by_day.get(day, 0) + 1
    _blocked_by_path[path] = _blocked_by_path.get(path, 0) + 1
    if len(_blocked_by_day) > _BLOCKED_KEEP_DAYS:
        for stale in sorted(_blocked_by_day)[:-_BLOCKED_KEEP_DAYS]:
            _blocked_by_day.pop(stale, None)


def auth_limit_stats() -> dict:
    """限流拦截的只读快照（运维总览用）。进程内计数：重启归零。"""
    return {
        "total": _blocked_total,
        "today": _blocked_by_day.get(time.strftime("%Y-%m-%d", time.gmtime()), 0),
        "by_path": dict(_blocked_by_path),
        "started_at": _started_at,
    }


async def auth_rate_limit_middleware(request: Request, call_next):
    """对 /auth/* 的请求限流（凭证端点严格、其余宽松）。

    挂中间件而不是写在每个路由里：这些端点大多是公开的，限流要在读请求体、查库之前生效；
    而路由函数要等到依赖注入完成才执行，那时代价已经付了。
    """
    path = request.url.path
    if path.startswith("/auth"):
        ip = await resolve_client_ip(request) or "-"
        # 凭证端点按 (IP, 路径) 分桶：一次登录爆破不该把同 IP 的注册配额也吃掉
        if path in _AUTH_CREDENTIAL_PATHS:
            retry_after = _auth_credential_window.hit(f"{ip}:{path}")
        else:
            retry_after = _auth_other_window.hit(ip)
        if retry_after:
            _count_blocked(path)
            return JSONResponse(
                status_code=429,
                content={"detail": f"请求过于频繁，请 {retry_after} 秒后再试"},
                headers={"Retry-After": str(retry_after)},
            )
    return await call_next(request)


async def maintenance_middleware(request: Request, call_next):
    """维护模式拦截（硬维护 503；软维护响应带头，前端据此显隐提示）"""
    path = request.url.path
    bypass = path in _EXACT_BYPASS or path.startswith(_PREFIX_BYPASS)

    # 硬维护（自动启动/关闭 或 管理员手动）：503 拦截
    is_hard = maintenance.hard_active()
    if is_hard and not bypass:
        msg = maintenance.get_msg()
        return JSONResponse(
            status_code=503,
            content={"detail": msg["hard_body"], "maintenance": True, "hard": True, "msg": msg}
        )

    # 软维护（手动）：API 正常但前端显示提示。
    # 状态查询走 manager 的 TTL 缓存，开关切换（manager 写入）即时失效缓存。
    response = await call_next(request)
    if maintenance.is_soft():
        # 所有响应（含 bypass）都带维护头，前端据此显隐提示且不会误判"已关闭"
        response.headers["X-Maintenance"] = "true"
        if not path.startswith("/maintenance-msg"):
            response.headers["X-Maintenance-Hard"] = str(is_hard)
    return response


def register_middlewares(app: FastAPI) -> None:
    """统一注册所有 HTTP 中间件（CORS + 请求日志 + IP 追踪 + /auth 限流 + 维护模式拦截）

    Starlette 后注册者先执行，执行顺序：
    1. request_logging_middleware（请求日志 + Request ID，最外层，保证所有请求都被记录，
       包括被限流拒掉的那些——429 也要在日志里看得见）
    2. auth_rate_limit_middleware（/auth/* 限流）
    3. client_ip_middleware（IP 追踪）
    4. maintenance_middleware（维护拦截，最内层）
    5. CORS（框架内置，最先注册）
    """
    # ── CORS（默认不启用：同源代理部署不需要跨域） ──
    # ALLOWED_ORIGINS（逗号分隔）配置后启用：
    #   - 含 "*"：允许所有来源，但不携带凭据（浏览器规范禁止 "*" 与凭据组合）
    #   - 显式域名列表：允许带凭据的精确跨域
    from app.config import settings
    _origins = settings.allowed_origins
    if _origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=_origins,
            allow_credentials="*" not in _origins,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # ── 自定义中间件（后注册者先执行） ──
    # 顺序：先注册内层，后注册外层
    app.middleware("http")(maintenance_middleware)     # 内层
    app.middleware("http")(client_ip_middleware)       # 中层
    app.middleware("http")(auth_rate_limit_middleware) # 外层之一：限流要在读请求体之前生效
    app.middleware("http")(request_logging_middleware) # 最外层（最后注册，最先执行）
