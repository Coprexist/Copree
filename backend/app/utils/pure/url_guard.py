"""出站目标守卫（纯函数，只判断"是不是内网"）。

**为什么需要它**（2026-09-13）：用户可填的 base_url 会让服务器替他们发请求。
不设限就等于给每个注册用户一个内网端口扫描器——用无效 key 探测状态码
（200/401/404/302/连接拒绝/超时）就能画出内网服务地图，实测已确认可行。

**为什么不能简单"一律只许公网"**：平台自己就在合法使用内网地址
（preset 的 Ollama http://localhost:11434、容器部署时的 embedding http://host.docker.internal:11434），
而用户也合法地指向自己的局域网 LLM。所以分界线不是"URL 长什么样"，
而是"**这个地址是不是用户已经声明过要用的那一个**"——见 base_url_registry。
**两个入口，按上下文挑**：纯判定的逻辑只有一份（`classify_address`）；
落成"这个 URL 是不是内网"时，同步上下文用 `is_private_target`，async 上下文必须用
`is_private_target_async`——解析域名这一步在图省事的时候最贵：本机解析不到一个域名实测要
16 秒（2026-10-28，`example.com`），同步解析摆在 async 函数里就是把整个 worker 停住。
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
from urllib.parse import urlsplit

# 明显是本机的名字（DNS 之前先挡掉，省一次解析）
_LOCAL_NAMES = ("localhost", "localhost.localdomain")
_LOCAL_SUFFIXES = (".local", ".internal", ".lan", ".home")


def _split(url: str):
    raw = (url or "").strip()
    if "://" not in raw:
        raw = "http://" + raw
    return urlsplit(raw)


def host_port(url: str) -> str:
    """规范化成 host:port，用于白名单比较（默认端口按 scheme 补全）。"""
    p = _split(url)
    host = (p.hostname or "").lower()
    port = p.port or (443 if p.scheme == "https" else 80)
    return f"{host}:{port}"


# ═══════════════════════════════════════════════════════════════
# 地址分类：internal（内网/本机）/ unusable（特殊用途，拨不到）/ ok（公网）
#   站内唯一一处定义"哪些地址算内网"（出站守卫与联网工具共用）。
#   为什么不用 is_private 一句话了事：IPv6 的 is_private 把 2001::/23 整段算进去，
#   于是 2001::1f0d:5e0a（Teredo，本机没有隧道、拨过去必然失败）会被说成"内网地址"，
#   既误导用户，又会把同域名下那个能用的公网 IPv4 一起毙掉（2026-09-16 用户实测）。
# ═══════════════════════════════════════════════════════════════
# 真正的内网/本机（IPv6；IPv4 直接交给 ipaddress 的判定）
_INTERNAL_V6 = tuple(ipaddress.ip_network(n) for n in (
    "::1/128",      # 回环
    "::/128",       # 未指定
    "fc00::/7",     # 唯一本地地址（ULA）
    "fe80::/10",    # 链路本地
    "fec0::/10",    # 站点本地（已废弃，仍是内网）
))
# IANA 特殊用途段里"不是内网、但本机也拨不到"的那些：拨了必然失败，只会白等一次超时
_UNUSABLE_V6 = tuple(ipaddress.ip_network(n) for n in (
    "2001::/32",      # Teredo（隧道端点，本机没有隧道）
    "2001:2::/48",    # Benchmarking
    "2001:10::/28",   # ORCHID（旧）
    "2001:20::/28",   # ORCHIDv2
    "2001:db8::/32",  # 文档用
    "100::/64",       # Discard-only
    "3fff::/20",      # 文档用
))
_NAT64_V6 = ipaddress.ip_network("64:ff9b::/96")     # NAT64 良知前缀（低 32 位是 v4）


def _v4_is_internal(v4: ipaddress.IPv4Address) -> bool:
    return bool(v4.is_private or v4.is_loopback or v4.is_link_local
                or v4.is_reserved or v4.is_multicast or v4.is_unspecified)


def _embedded_v4(addr: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    """过渡/转换地址里真正要连的 IPv4；没有就返回 None。

    必须按内嵌的 v4 判：2002:7f00:1:: 就是 127.0.0.1，64:ff9b::a00:1 就是 10.0.0.1——
    这正是绕 SSRF 的经典写法。
    """
    if addr.ipv4_mapped:
        return addr.ipv4_mapped
    if addr.sixtofour:
        return addr.sixtofour
    if addr in _NAT64_V6:
        return ipaddress.IPv4Address(addr.packed[-4:])
    return None


def classify_address(ip: str) -> str:
    """地址分三档：internal（内网/本机，必须拒绝）/ unusable（特殊用途，跳过）/ ok（公网，可拨）"""
    try:
        addr = ipaddress.ip_address(str(ip).split("%")[0])
    except ValueError:
        return "unusable"
    if addr.version == 4:
        return "internal" if _v4_is_internal(addr) else "ok"
    embedded = _embedded_v4(addr)                        # type: ignore[arg-type]
    if embedded is not None:
        return "internal" if _v4_is_internal(embedded) else "ok"
    if addr.is_loopback or addr.is_multicast or addr.is_unspecified or any(addr in n for n in _INTERNAL_V6):
        return "internal"
    if any(addr in n for n in _UNUSABLE_V6):
        return "unusable"
    return "ok"


def _ip_is_internal(ip: str) -> bool:
    """出站守卫的口径：只要不是正常公网地址就要用户先登记（含拨不到的特殊用途段）"""
    return classify_address(ip) != "ok"


# 解析结果的过程内缓存。TTL 短是有意的：地址会变（CDN/GSLB），缓存太久会让"刚改完解析"
# 的机器仍按老地址判。缓存失败结果同样重要——解析不出去的那十几秒，不该每次点击重付一遍。
_RESOLVE_TTL_SECONDS = 60.0
_RESOLVE_CACHE_MAX = 256
_resolve_cache: dict[str, tuple[float, tuple[str, ...]]] = {}


def clear_resolution_cache() -> None:
    """清解析缓存（换网络、改 DNS 后想让判定立刻生效时用；测试也用它）。"""
    _resolve_cache.clear()


def resolve_host_addresses(host: str) -> tuple[str, ...]:
    """域名 → 地址串（去重保序）；解析失败返回空表。

    空表在 `_judge` 里表现为"不当私网"，交给请求阶段报"无法连接"——
    免得把"域名写错了"误报成安全拦截。
    """
    cached = _resolve_cache.get(host)
    if cached is not None:
        expires_at, addresses = cached
        if expires_at > time.monotonic():
            return addresses
        _resolve_cache.pop(host, None)
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        addresses = ()
    else:
        addresses = tuple(dict.fromkeys(info[4][0] for info in infos))
    if len(_resolve_cache) >= _RESOLVE_CACHE_MAX:
        # 这本就是"短时间内的复用"，不做 LRU：满了整体丢掉，比维护淘汰顺序划算
        _resolve_cache.clear()
    _resolve_cache[host] = (time.monotonic() + _RESOLVE_TTL_SECONDS, addresses)
    return addresses


def _judge(host: str, addresses: tuple[str, ...]) -> bool:
    if host in _LOCAL_NAMES or host.endswith(_LOCAL_SUFFIXES):
        return True
    if _looks_like_ip(host):
        return _ip_is_internal(host)      # IP 字面量：没有可解析的东西
    return any(_ip_is_internal(address) for address in addresses)


def _needs_resolution(host: str) -> bool:
    return not (host in _LOCAL_NAMES or host.endswith(_LOCAL_SUFFIXES) or _looks_like_ip(host))


def is_private_target(url: str) -> bool:
    """目标是否指向内网/本机（同步版：会真的解析域名，只给同步上下文用）。

    域名解析带 60 秒缓存，同一个主机在缓存期内只问一次 DNS。
    """
    host = (_split(url).hostname or "").lower()
    if not host:
        return False
    return _judge(host, resolve_host_addresses(host) if _needs_resolution(host) else ())


async def is_private_target_async(url: str) -> bool:
    """同上，但域名解析丢到线程里跑——不把事件循环停在 DNS 上。

    为什么必须有这一版：同步解析最坏要等十几秒（2026-10-28 实测 `example.com` 16.01s），
    而判定跑在 async 请求路径上：一次"测试连接"就能让所有人的请求一起卡住。
    """
    host = (_split(url).hostname or "").lower()
    if not host:
        return False
    if not _needs_resolution(host):
        return _judge(host, ())
    return _judge(host, await asyncio.to_thread(resolve_host_addresses, host))


def _looks_like_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False
