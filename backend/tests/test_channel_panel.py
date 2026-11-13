"""通道控制台反代 —— 平台只做票据与转发，具体通道的路径口径归插件

三条不变量：
1. 票据按 (plugin_id, instance) 限定：一条通道的票据在另一条通道上无效；
2. 路径是通用的 /channels/{plugin_id}/panel/{instance}/...：核心不认识任何具体通道，
   加一条新通道不需要改平台代码；
3. 改写与入口由插件的 panel_rewrite / panel_entry 提供，平台只回调和透传。
"""
import time


async def test_ticket_is_scoped_to_one_plugin_and_one_instance():
    from app.routers import channel_panel

    assert channel_panel.panel_base("qq-napcat", "agent-7") == "/channels/qq-napcat/panel/agent-7"
    ticket = channel_panel._sign("qq-napcat", "agent-7", 42, int(time.time()) + 60)
    assert channel_panel._verify(ticket, "qq-napcat", "agent-7")
    # 换个通道 / 换个实例都不认：票据是"这条通道的这个控制台"的入场券
    assert not channel_panel._verify(ticket, "qq-channel", "agent-7")
    assert not channel_panel._verify(ticket, "qq-napcat", "agent-8")
    assert not channel_panel._verify("garbage", "qq-napcat", "agent-7")
    expired = channel_panel._sign("qq-napcat", "agent-7", 42, int(time.time()) - 1)
    assert not channel_panel._verify(expired, "qq-napcat", "agent-7")


async def test_route_paths_are_generic():
    from app.routers import channel_panel, get_all_routers

    paths = {getattr(r, "path", "") for r in channel_panel.router.routes}
    assert "/me/agents/{agent_id}/channels/{plugin_id}/panel" in paths
    assert "/channels/{plugin_id}/panel/{instance}/{rest:path}" in paths
    # 核心路由里不该再出现任何具体通道名（这条是"去 QQ 化"的验收点）
    for router in get_all_routers():
        for route in router.routes:
            assert "qq-napcat" not in getattr(route, "path", ""), getattr(route, "path", "")


async def test_proxy_rejects_traversal_and_missing_cookie():
    from fastapi import HTTPException
    from starlette.requests import Request

    from app.routers import channel_panel

    def request(query: bytes = b"", headers=()):
        return Request({
            "type": "http", "http_version": "1.1", "method": "GET", "scheme": "http",
            "path": "/channels/qq-napcat/panel/agent-1/webui/", "raw_path": b"/",
            "query_string": query, "headers": list(headers),
            "client": ("127.0.0.1", 1234), "server": ("testserver", 80),
        })

    for rest in ("a/../../api", "../x"):
        try:
            await channel_panel.panel_proxy("qq-napcat", "agent-1", rest, request())
        except HTTPException as e:
            assert e.status_code == 400, (rest, e.status_code)
        else:
            raise AssertionError("越界的路径该被拒：" + rest)

    # 没票据也没 cookie：连插件都不用问，直接 403
    try:
        await channel_panel.panel_proxy("qq-napcat", "agent-1", "webui/", request())
    except HTTPException as e:
        assert e.status_code == 403
    else:
        raise AssertionError("没有票据/cookie 的请求该被拒")


async def test_napcat_panel_adapter_prefixes_its_own_paths():
    """NapCat 的事实留在插件里：入口在 webui/ 下，绝对路径要补反代基路径"""
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "plugins" / "qq-napcat" / "panel.py"
    spec = importlib.util.spec_from_file_location("napcat_panel_under_test", path)
    panel = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(panel)

    base = "/channels/qq-napcat/panel/agent-7"
    assert panel.entry("TICKET") == "webui/?t=TICKET"

    page = '<html><head></head><link href="/webui/assets/app.css">' \
           '<style>body{background:url(/webui/bg.png)}</style></html>'
    out = panel.rewrite(page, base)
    assert f'"{base}/webui/assets/app.css"' in out
    assert f"url({base}/webui/bg.png)" in out
    assert f'"__BASE__"' not in out
    assert f'var B="{base}"' in out, "shim 要带上这条实例的基路径"
    assert out.endswith("</html>")
