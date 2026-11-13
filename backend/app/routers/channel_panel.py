"""通道控制台反代 —— 把某个通道插件自带的运维控制台挂到平台路径下

为什么反代而不是让用户直连协议端的 6099：那个控制台能改协议端的一切（换号、改 WS、装插件），
不该暴露到公网，也不该让每个用户手里攥着一份 webui token。平台代用户连，用户只拿到一条路径。

平台对这个控制台**一无所知**——"控制台挂在哪个子路径、前端把绝对路径写死在哪"都是具体通道
的事实。所以这里只做四件通用的事：

1. 票据：iframe / 新标签里的 js/css/SSE 带不上平台的 Bearer，首屏走一次性票据（?t=...），
   验完换成 Path 限定在该实例下的 HttpOnly cookie，之后浏览器自动带上。票据里带 user_id，
   换个账号进来过不了；也带 plugin_id，一条通道的票据在另一条通道上无效。
2. 转发：地址与鉴权头由插件的 `panel_client()` 给，平台只补 hop-by-hop 头与查询串。
3. 改写：文本（HTML/CSS）交给插件的 `panel_rewrite(text, base)`；插件没有这个方法就原样透传。
4. 入口：票据接口返回的落地路径由插件的 `panel_entry(ticket)` 给（如 NapCat 的 `webui/?t=...`）。

没有 `panel_client` / `panel_entry` 的通道在这两个接口上一律 404，卡片上也就不会有这个按钮。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

from app.config import settings
from app.database import get_db
from app.services.plugin import channel
from app.services.plugin.channel import instance_of
from app.utils.auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(tags=["通道控制台"])

# 票据与 cookie：都只活一个小时，过期回卡片重新点一次（比"永不过期的链接"安全）。
# cookie 按路径（= 这条通道的这个实例）限定，同一个人开两条通道的控制台互不覆盖。
_COOKIE = "channel_panel"
_TICKET_TTL = 3600
# 不该原样转发的头（逐跳头与由我们重算的长度/编码）
_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
    "transfer-encoding", "upgrade", "host", "content-length", "content-encoding",
}


def panel_base(plugin_id: str, instance: str) -> str:
    """反代基路径：页面里的绝对路径都改成从这里开始"""
    return f"/channels/{plugin_id}/panel/{instance}"


def _sign(plugin_id: str, instance: str, user_id: int, expires_at: int) -> str:
    message = f"{plugin_id}:{instance}:{user_id}:{expires_at}".encode()
    mac = hmac.new(settings.jwt_secret_key.encode(), message, hashlib.sha256).digest()[:16]
    return f"{user_id}:{expires_at}:{base64.urlsafe_b64encode(mac).decode().rstrip('=')}"


def _verify(ticket: str, plugin_id: str, instance: str) -> bool:
    """票据有效、没过期，且确实是为这条通道的这个实例签的"""
    try:
        user_id, expires_at, mac = ticket.split(":")
        expires = int(expires_at)
    except (ValueError, AttributeError):
        return False
    if expires <= int(time.time()):
        return False
    expected = _sign(plugin_id, instance, int(user_id), expires).split(":")[2]
    return hmac.compare_digest(mac, expected)


def _plugin_of(plugin_id: str, instance: str):
    from app.services.infrastructure.plugin_registry import get_by_owner

    return get_by_owner(plugin_id, instance)


async def _close(upstream: httpx.Response, client: httpx.AsyncClient) -> None:
    await upstream.aclose()
    await client.aclose()


async def _panel_client_of(plugin_id: str, instance: str):
    """通道实例及其控制台客户端；没有控制台就 404（卡片上不该有那个按钮）"""
    plugin = _plugin_of(plugin_id, instance)
    if plugin is None or getattr(plugin, "panel_client", None) is None:
        raise HTTPException(404, "这条通道没有控制台")
    client = await plugin.panel_client()
    if not client.configured:
        raise HTTPException(400, "协议端控制台地址或 token 未配置")
    return plugin, client


@router.post("/me/agents/{agent_id}/channels/{plugin_id}/panel")
async def panel_ticket(
    agent_id: int,
    plugin_id: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """发一张打开通道控制台的票据（只有这个 AI 的持有者能拿）

    路径跟着 plugin_id 走：没有控制台的通道在这里 404，卡片上也就不会有这个按钮。
    """
    try:
        await channel.owned_agent(db, agent_id, int(user["user_id"]))
    except channel.NotOwned as e:
        raise HTTPException(403, str(e))
    instance = instance_of(agent_id)
    plugin, _client = await _panel_client_of(plugin_id, instance)
    entry = getattr(plugin, "panel_entry", None)
    if entry is None:
        raise HTTPException(404, "这条通道没有控制台")
    ticket = _sign(plugin_id, instance, int(user["user_id"]), int(time.time()) + _TICKET_TTL)
    return {"url": f"{panel_base(plugin_id, instance)}/{entry(ticket)}"}


@router.api_route(
    "/channels/{plugin_id}/panel/{instance}/{rest:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
)
async def panel_proxy(plugin_id: str, instance: str, rest: str, request: Request):
    """把该实例的控制台原样转出去（地址与鉴权都由插件的 panel_client 补）"""
    # .. 段能穿出协议端的控制台根：反代只该看见"这个控制台之下的路径"
    if ".." in [part for part in rest.split("/")]:
        raise HTTPException(400, "非法路径")
    base = panel_base(plugin_id, instance)
    ticket = request.query_params.get("t", "")
    if ticket:
        if not _verify(ticket, plugin_id, instance):
            raise HTTPException(403, "票据无效或已过期")
        rest_params = [(k, v) for k, v in request.query_params.multi_items() if k != "t"]
        target_url = f"{base}/{rest}"
        if rest_params:
            target_url += "?" + urlencode(rest_params)
        response = RedirectResponse(target_url, status_code=302)
        response.set_cookie(
            _COOKIE, ticket, path=base,
            httponly=True, samesite="lax", max_age=_TICKET_TTL,
        )
        return response
    if not _verify(request.cookies.get(_COOKIE, ""), plugin_id, instance):
        raise HTTPException(403, "控制台会话已过期，回到通道卡片重新打开")

    plugin, client = await _panel_client_of(plugin_id, instance)

    upstream_url = f"{client.base}/{rest}"
    query = [(k, v) for k, v in request.query_params.multi_items() if k != "t"]
    if query:
        upstream_url += "?" + urlencode(query)

    headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP}
    headers.update(await client.auth_headers())
    body = await request.body()

    http = httpx.AsyncClient(timeout=None, follow_redirects=False)
    try:
        upstream = await http.send(
            http.build_request(request.method, upstream_url, headers=headers, content=body),
            stream=True,
        )
    except httpx.HTTPError as e:
        await http.aclose()
        raise HTTPException(502, f"连不上协议端：{e}")

    out_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in _HOP}
    content_type = upstream.headers.get("content-type", "")
    # 只有这两种文本要改写；其余（图片、字体、SSE）原样流过去
    if content_type.startswith(("text/html", "text/css")):
        text = (await upstream.aread()).decode("utf-8", errors="replace")
        await _close(upstream, http)
        rewrite = getattr(plugin, "panel_rewrite", None)
        if rewrite is not None:
            text = rewrite(text, base)
        return Response(
            text, status_code=upstream.status_code,
            headers=out_headers, media_type=content_type,
        )
    return StreamingResponse(
        upstream.aiter_raw(), status_code=upstream.status_code, headers=out_headers,
        background=BackgroundTask(_close, upstream, http),
    )
