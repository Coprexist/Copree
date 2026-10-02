"""授权边界的静态守卫 —— 权限判定只允许走统一入口。

背景（2026-09-13 公网自测报告）：/fs/public/{file_id} 匿名拖全站文件、
/gm/{group_id}/messages 越权读任意群、WS subscribe 无成员校验、
/admin/tools/backpack 漏挂管理员鉴权、require_agent_access 信 JWT 里的 role。

修完之后把「漏挂」变成会红的用例：路由清单里少一个依赖就报错，而不是等下一次渗透测试。
约定见 docs/guides/安全与权限模型.md。

本文件只做静态检查与纯函数验证（不连库、不建表）。
"""
import inspect
import json
import os
import tempfile

from app.main import app


def _dependency_names(route) -> set[str]:
    """递归收集路由依赖链上的可调用对象名"""
    names: set[str] = set()
    stack = [route.dependant]
    while stack:
        dependant = stack.pop()
        call = getattr(dependant, "call", None)
        if call is not None:
            names.add(getattr(call, "__name__", str(call)))
        stack.extend(getattr(dependant, "dependencies", []) or [])
    return names


def _find_route(path: str):
    for route in app.routes:
        if getattr(route, "path", None) == path:
            return route
    return None


def _routes_missing_dependency(dependency: str, prefix: str) -> list[str]:
    """prefix 下缺少指定依赖的 HTTP 路由（WebSocket 路由不走 Depends，另行用例）"""
    missing: list[str] = []
    for route in app.routes:
        path = getattr(route, "path", "") or ""
        if not path.startswith(prefix):
            continue
        methods = sorted(getattr(route, "methods", None) or ())
        if not methods:
            continue
        if dependency not in _dependency_names(route):
            missing.append(",".join(methods) + " " + path)
    return sorted(missing)


async def test_admin_routes_all_require_admin():
    """/admin 下不允许漏挂管理员鉴权（自测报告 #6）"""
    missing = _routes_missing_dependency("require_admin", "/admin")
    assert not missing, "以下 /admin 路由缺少 require_admin：" + repr(missing)


async def test_group_read_routes_require_membership():
    """群消息 / 群详情 / 群成员只对群成员开放（自测报告 #2）"""
    for path in (
        "/gm/{group_id}/messages",
        "/groups/{group_id}",
        "/groups/{group_id}/members",
    ):
        route = _find_route(path)
        assert route is not None, path + " 未注册"
        assert "require_group_member" in _dependency_names(route), (
            path + " 缺少 require_group_member：任意登录用户可读任意群"
        )


async def test_ws_group_subscribe_checks_membership():
    """WS 订阅群与 REST 同口径（自测报告 #2 的实时入口）"""
    from app.routers import ws

    src = inspect.getsource(ws.websocket_endpoint)
    assert "is_group_member" in src, "WS 群订阅必须校验成员身份，否则可越权听任意群直播"


async def test_chat_user_routes_require_login():
    """用户信息与好友列表不得匿名可读（自测报告 #5、#9）"""
    for path in ("/chat/user/{user_id}", "/chat/user/{user_id}/friends"):
        route = _find_route(path)
        assert route is not None, path + " 未注册"
        assert "get_current_user" in _dependency_names(route), path + " 缺少登录校验"


async def test_agent_access_rechecks_role_from_db():
    """JWT 里的 role 是签发快照，权限判定必须回查 DB（自测报告 #8）"""
    from app.routers import deps
    from app.utils import auth

    assert "load_user_role" in inspect.getsource(deps.require_agent_access)
    assert "load_user_role" in inspect.getsource(auth.require_admin)


async def test_public_file_route_checks_maintenance_whitelist():
    """匿名下载只放行维护弹窗图片（自测报告 #1）"""
    from app.routers import files

    src = inspect.getsource(files.download_public_file)
    assert "is_public_file" in src, "/fs/public 必须走维护图片白名单，否则等于匿名拖全站文件"


def test_public_file_id_parsing():
    """白名单 URL 解析：只认 /fs/public/{id} 形态"""
    from app.services.infrastructure.maintenance import parse_public_file_id

    assert parse_public_file_id("/fs/public/12") == 12
    assert parse_public_file_id("/fs/public/12?thumb=1") == 12
    assert parse_public_file_id("/fs/public/abc") is None
    assert parse_public_file_id("https://cdn.example.com/a.png") is None
    assert parse_public_file_id("") is None


def test_whitelist_denies_by_default_and_allows_listed_files():
    """白名单语义：默认拒；只有维护图片列表里的 file_id 才放行"""
    from app.services.infrastructure.maintenance import MaintenanceManager

    with tempfile.TemporaryDirectory() as tmp:
        manager = MaintenanceManager(maint_dir=tmp, data_dir=tmp)
        assert manager.is_public_file(6) is False, "空列表必须全部拒绝"
        with open(os.path.join(tmp, "maintenance_images.json"), "w", encoding="utf-8") as f:
            f.write(json.dumps(["/fs/public/6", "https://cdn.example.com/a.png"]))
        assert manager.is_public_file(6) is True
        assert manager.is_public_file(7) is False, "未列入的文件必须拒绝"


async def test_docs_closed_in_production():
    """生产环境不暴露接口文档（自测报告 #4）"""
    from fastapi import HTTPException

    from app.config import settings
    from app.routers.swagger_docs import _ensure_docs_enabled

    original = settings.environment
    settings.environment = "production"
    try:
        try:
            _ensure_docs_enabled()
        except HTTPException as exc:
            assert exc.status_code == 404
        else:
            raise AssertionError("生产环境 /docs 必须 404")
    finally:
        settings.environment = original


async def test_federation_outbound_tls_verification_enabled():
    """联邦出站不得关闭 TLS 校验；自签对端走 FEDERATION_CA_BUNDLE（自测报告 #7）"""
    import importlib

    from app.services.federation import federation_service

    src = inspect.getsource(federation_service.peer_http_client)
    assert "verify" in src and "federation_ca_bundle" in src
    for module_name in ("app.services.federation.federation_manager", "app.routers.federation_ws"):
        module = importlib.import_module(module_name)
        assert "verify=False" not in inspect.getsource(module), (
            module_name + " 又出现了 verify=False，联邦出站请走 peer_http_client()"
        )

# ═══════════════════════════════════════════════════════════════
# SSRF 防护：地址分类 + 多地址域名
#   背景（2026-09-16 用户反馈）：lite.duckduckgo.com 被报「禁止访问内网地址 (2001::1f0d:5e0a)」，
#   而那个地址在 2001::/32（Teredo，本机拨不到），旁边还有个好好的公网 IPv4。
# ═══════════════════════════════════════════════════════════════

def test_ssrf_address_classification():
    """内网一律拒；特殊用途段只是"拨不到"（跳过，不能报成内网）；公网放行"""
    from app.utils.pure.url_guard import classify_address as classify

    for ip in ("127.0.0.1", "10.1.2.3", "192.168.1.1", "172.16.0.9", "169.254.169.254",
               "0.0.0.0", "::1", "fd00::1", "fe80::1", "::ffff:127.0.0.1",
               "2002:7f00:1::", "64:ff9b::a00:1"):
        assert classify(ip) == "internal", f"{ip} 是内网/本机，必须拒绝"
    for ip in ("2001::1f0d:5e0a", "2001:db8::1", "100::1"):
        assert classify(ip) == "unusable", f"{ip} 拨不到，但不该说成内网"
    for ip in ("104.244.46.71", "20.205.243.166", "2606:4700::1111"):
        assert classify(ip) == "ok", f"{ip} 是公网，必须放行"


def test_ssrf_resolve_keeps_the_usable_address_and_still_blocks_internal():
    """一个坏地址不能拖死整单；但只要有内网地址就照旧整单拒绝（DNS rebinding）"""
    import socket

    from app.tools.file_operations import web_fetch as wf

    original = socket.getaddrinfo

    def mixed(host, port, *a, **k):
        return [(socket.AF_INET6, 1, 6, "", ("2001::1f0d:5e0a", 0, 0, 0)),
                (socket.AF_INET, 1, 6, "", ("104.244.46.71", 0))]

    socket.getaddrinfo = mixed
    try:
        ips, block = wf.resolve_candidates("https://lite.duckduckgo.com/lite/?q=x")
    finally:
        socket.getaddrinfo = original
    assert block is None and ips == ["104.244.46.71"], "应挑出公网 IPv4，而不是整单拒绝"

    def half_internal(host, port, *a, **k):
        return [(socket.AF_INET, 1, 6, "", ("192.168.1.10", 0)),
                (socket.AF_INET, 1, 6, "", ("104.244.46.71", 0))]

    socket.getaddrinfo = half_internal
    try:
        ips, block = wf.resolve_candidates("https://evil.example/")
    finally:
        socket.getaddrinfo = original
    assert ips == [] and "内网" in block, "一半公网一半内网：必须整单拒绝"


def test_ssrf_pins_the_vetted_address_but_keeps_the_hostname():
    """真正拨的是复检过的 IP；Host 头与 SNI 仍用原域名（证书照常校验）"""
    from app.tools.file_operations.web_fetch import pin_url

    assert pin_url("https://example.com/a?b=1", "1.2.3.4") == "https://1.2.3.4/a?b=1"
    assert pin_url("http://example.com:8080/x", "2001:db8::1") == "http://[2001:db8::1]:8080/x"


def test_ssrf_localhost_is_refused_without_dns():
    """本机名直接拒（连 DNS 都不问）"""
    from app.tools.file_operations.web_fetch import resolve_candidates

    ips, block = resolve_candidates("http://localhost:8000/admin")
    assert ips == [] and "本机" in block
    ips, block = resolve_candidates("http://nas.local/")
    assert ips == [] and "本机" in block

# ── 登录防爆破（2026-09 补：失败无痕 = 撞库隐形）────────────

def test_auth_endpoints_go_through_the_rate_limit_middleware():
    """凭证端点必须挂在限流中间件下：漏挂就是给撞库留一条不限速的入口"""
    from app.middleware import _AUTH_CREDENTIAL_PATHS, auth_rate_limit_middleware

    registered = [m.kwargs.get("dispatch") for m in app.user_middleware]
    assert auth_rate_limit_middleware in registered, "auth 限流中间件没注册"
    for path in ("/auth/login", "/auth/register", "/auth/send-verification-code"):
        assert path in _AUTH_CREDENTIAL_PATHS, path + " 不在严格配额里"


def test_login_route_counts_and_audits_failures():
    """失败登录要计次数、要留痕——只记成功那条等于看不见撞库"""
    from app.routers import auth as auth_router

    src = inspect.getsource(auth_router.login)
    assert "count_failure" in src, "/auth/login 失败必须计数（否则锁定无从谈起）"
    assert "audit_failure" in src, "/auth/login 失败必须写审计（撞库不能隐形）"


def test_failed_login_audit_uses_its_own_session():
    """审计要自己开会话提交：请求会话在 get_db 里遇异常就回滚，挂上去等于没写"""
    from app.services.infrastructure import login_guard

    src = inspect.getsource(login_guard.audit_failure)
    assert "async_session" in src and "commit" in src, "失败登录审计必须独立提交"
