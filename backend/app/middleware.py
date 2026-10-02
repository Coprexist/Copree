"""
HTTP 中间件 — 请求日志/Request ID + CORS + 请求 IP 追踪 + 维护模式拦截。

IP 追踪只取 uvicorn 解析后的 request.client，不自行解析 X-Forwarded-For：
uvicorn 的 ProxyHeadersMiddleware 仅信任 --forwarded-allow-ips 内的代理
（默认 127.0.0.1，即默认不信任任何代理头），自行解析会绕过该可信检查。

注意：当前 docker 端口映射部署下（浏览器 → docker-proxy 网关 → vite → backend），
vite 看到的客户端源是网关 IP，后端审计 IP 为网关地址；要还原真实客户端 IP，
需在宿主层加反向代理并配置 uvicorn --forwarded-allow-ips 指向该代理。
"""
import logging
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.services.infrastructure.maintenance import maintenance
from app.utils.auth import set_current_request_ip
from app.utils.rate_limit import SlidingWindow

logger = logging.getLogger(__name__)

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
        logger.info(
            f"[{request_id}] {request.method} {request.url.path} → "
            f"{response.status_code} ({elapsed_ms:.0f}ms)"
        )
    return response


async def client_ip_middleware(request: Request, call_next):
    """记录请求 IP 到 contextvar（供审计日志使用）"""
    try:
        set_current_request_ip(request.client.host if request.client else None)
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
        ip = request.client.host if request.client else "-"
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
