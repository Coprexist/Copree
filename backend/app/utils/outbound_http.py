"""出站请求的唯一一扇门：自己挑地址、把选中的 IP 钉进连接、每一跳重定向都复检。

**为什么不是"先判一下再让 httpx 去拨"**：判定要解析域名，拨号时 httpx 会再解析一次——
两次之间地址可以变（DNS rebinding），于是"判定的结果"和"真正连的地址"是两个东西。
所以这里把顺序反过来：**先解析一次，再拿这一份解析结果去连**，Host 头与 SNI 仍用原域名
（证书照常校验），每一跳重定向重新解析、重新判定。

一个域名可能解析出多个地址，其中只有一个是坏的（2026-09-16 实测：lite.duckduckgo.com 旁边
挂了个 2001::1f0d:5e0a，本机没有 Teredo 隧道，拨过去只会 Network is unreachable）。
所以口径是：**不可拨的特殊用途地址跳过；只要出现一个内网地址就整单拒绝**——后者是 DNS
rebinding 的经典打法（一半公网一半内网），挑"能用的那个"等于自己把门打开。

"哪些地址算内网"只在 `app/utils/pure/url_guard.py` 定义一处；这里只做"挑、钉、复检"。
调用方：网页取数（web_fetch.safe_get）与供应商探针（api_probe.probe_provider）共用；
探针比取数多一条政策——**已登记的内网地址可以放行**（用户自己的局域网 LLM），
由 `allow_internal` 这个判据传入，而不是在探针里另写一套解析。
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx

from app.utils.pure.url_guard import classify_address, resolve_host_addresses

logger = logging.getLogger(__name__)

# 本机名字：不走 DNS 就能给出地址（"本机"的含义就是 127.0.0.1/::1）
_LOOPBACK_NAMES = ("localhost", "localhost.localdomain")
_LOCAL_SUFFIXES = (".local",)
# 超时类异常的默认文案（它们常常自带空字符串消息）
_TIMEOUT_LABELS = {
    httpx.ConnectTimeout: "连接超时",
    httpx.ReadTimeout: "读取超时",
    httpx.WriteTimeout: "写入超时",
}

# 让调用方按原因分类：private=内网目标被拒 / dns=解析失败 / unusable=只解析到不可拨地址
# / url=URL 本身不完整 / timeout / network / redirect
BLOCKED_PRIVATE = "private"
BLOCKED_DNS = "dns"
BLOCKED_UNUSABLE = "unusable"
BLOCKED_URL = "url"
BLOCKED_TIMEOUT = "timeout"
BLOCKED_NETWORK = "network"
BLOCKED_REDIRECT = "redirect"


class BlockedFetch(Exception):
    """出站目标被拦下 / 地址不可用。msg 是给用户看的一句话，kind 给调用方分类。"""

    def __init__(self, message: str, *, kind: str = BLOCKED_PRIVATE):
        super().__init__(message)
        self.kind = kind


@dataclass(slots=True)
class _Target:
    """一次解析的结果：原域名、Host 头用的 netloc、可拨地址、以及（被拒时的）原因。"""

    host: str = ""
    netloc: str = ""
    candidates: list[str] = field(default_factory=list)
    error: str = ""
    kind: str = BLOCKED_PRIVATE


def pin_url(url: str, ip: str) -> str:
    """把 URL 的主机名换成已复检通过的 IP（Host 头与 SNI 保持原域名，证书照常校验）"""
    host = f"[{ip}]" if ":" in ip else ip
    return str(httpx.URL(url).copy_with(host=host))


def _resolve(url: str, allow_internal=None) -> _Target:
    """解析一次并分类。allow_internal(host_port, internal_ips) 返回 True 表示这些内网地址已登记。

    返回的 candidates 就是**接下来要拨的那几个地址**——判定与连接共用这一份结果。
    """
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").strip().lower()
        netloc = parsed.netloc.rsplit("@", 1)[-1]
    except ValueError:
        return _Target(error="URL 解析失败", kind=BLOCKED_URL)
    if not host:
        return _Target(error="URL 缺少主机名", kind=BLOCKED_URL)

    if host in _LOOPBACK_NAMES or host.endswith(_LOCAL_SUFFIXES):
        loopback = ["127.0.0.1", "::1"] if host in _LOOPBACK_NAMES else []
        if loopback and allow_internal and allow_internal(netloc, loopback):
            return _Target(host=host, netloc=netloc, candidates=["127.0.0.1"])
        return _Target(host=host, netloc=netloc, error=f"禁止访问本机/内网地址: {host}")

    addresses = resolve_host_addresses(host)     # 带缓存；解析失败返回空表
    if not addresses:
        return _Target(host=host, netloc=netloc, error=f"域名解析失败：{host}", kind=BLOCKED_DNS)

    internal: list[str] = []
    usable: list[str] = []
    unusable: list[str] = []
    for ip in addresses:
        kind = classify_address(ip)
        bucket = internal if kind == "internal" else usable if kind == "ok" else unusable
        if ip not in bucket:
            bucket.append(ip)
    if internal:
        if allow_internal and allow_internal(netloc, internal):
            internal.sort(key=lambda ip: ":" in ip)      # IPv4 优先：本机常没有 IPv6 出口
            return _Target(host=host, netloc=netloc, candidates=internal)
        return _Target(host=host, netloc=netloc,
                       error=f"禁止访问内网地址: {host} ({internal[0]})")
    if not usable:
        extra = f"，只解析到 {unusable[0]}（不可路由的特殊用途地址）" if unusable else ""
        return _Target(host=host, netloc=netloc,
                       error=f"没有可用地址：{host}{extra}", kind=BLOCKED_UNUSABLE)
    # IPv4 优先：本机没有 IPv6 出口时，先拨 v6 只会 Network is unreachable（用户机器实测）
    usable.sort(key=lambda ip: ":" in ip)
    return _Target(host=host, netloc=netloc, candidates=usable)


def resolve_candidates(url: str) -> tuple[list[str], str | None]:
    """默认政策（不放行任何内网）：返回 (可拨地址, 错误文案)。给 web_fetch 与测试用。"""
    target = _resolve(url)
    return (target.candidates, None) if not target.error else ([], target.error)


async def request_pinned(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    headers: dict | None = None,
    json=None,
    max_redirects: int = 0,
    allow_internal=None,
) -> httpx.Response:
    """按"自己挑、自己拨"的方式发一次请求，可跟随有限跳重定向（每跳重新解析与判定）。

    client 不要开 follow_redirects：重定向必须回过这一层，否则跨域跳到内网就绕过去了。
    """
    current = url
    for _ in range(max_redirects + 1):
        target = await asyncio.to_thread(_resolve, current, allow_internal)
        if target.error:
            raise BlockedFetch(target.error, kind=target.kind)
        https = urlparse(current).scheme == "https"
        resp: httpx.Response | None = None
        last_error: Exception | None = None
        for ip in target.candidates:
            try:
                resp = await client.request(
                    method,
                    pin_url(current, ip),
                    headers={**(headers or {}), "Host": target.netloc},
                    json=json,
                    # SNI 用原域名：证书校验与"服务端看到的域名"都跟直连一致
                    extensions={"sni_hostname": target.host} if https else None,
                )
                break
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as e:
                last_error = e        # 这个地址不通就换下一个：多地址域名不该被一个坏地址拖死
        if resp is None:
            assert last_error is not None
            # ConnectTimeout 这类异常自带空消息，别给用户看「（）」
            why = _TIMEOUT_LABELS.get(type(last_error)) or str(last_error) or type(last_error).__name__
            kind = BLOCKED_TIMEOUT if isinstance(last_error, tuple(_TIMEOUT_LABELS)) else BLOCKED_NETWORK
            raise BlockedFetch(f"连接失败：{target.host}（{why}）", kind=kind)
        location = resp.headers.get("location")
        if resp.is_redirect and location:
            current = str(resp.url.join(location))
            continue
        return resp
    raise BlockedFetch(f"重定向次数过多（>{max_redirects}）", kind=BLOCKED_REDIRECT)
