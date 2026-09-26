"""镜像轮播的契约守卫（"官网失败自动轮播镜像"）。

四条硬规则各自都要能验证：
1. **官方第一优先**——官方能通时，一次镜像都不许碰；
2. 官方失败才轮播，且**按表里的顺序**；
3. 镜像返回错误码也算失败，继续下一个；
4. 全都不行 → 抛官方那个错误（绝不假装成功）。

不连网：DNS 与 HTTP 客户端都换成假的（钉住的 IP 就是路由键）。
"""
from __future__ import annotations

import socket
from urllib.parse import urlparse

import httpx

from app.tools.file_operations import web_fetch as wf
from app.tools.file_operations.mirror_table import mirrors_for

# 假 DNS：每个域名给一个固定的"公网"IP（203.0.113.x 会被判成文档段，别用）
_DNS = {
    "raw.githubusercontent.com": "1.1.1.1",
    "ghproxy.net": "8.8.8.8",
    "gh-proxy.com": "8.8.4.4",
    "ghfast.top": "9.9.9.9",
}
_SRC = "https://raw.githubusercontent.com/a/b/c.txt"


class _Resp:
    def __init__(self, url: str, status: int = 200):
        self.status_code = status
        self.headers: dict = {}
        self.url = httpx.URL(url)
        self.text = "hello"
        self.content = b"hello"

    @property
    def is_redirect(self) -> bool:
        return False


class _Client:
    """假 client：按"钉住的 IP"给响应或抛错，并记录调用顺序"""

    def __init__(self, plan: dict):
        self.plan = plan
        self.calls: list[str] = []

    async def get(self, url, headers=None, extensions=None):
        ip = urlparse(url).hostname or ""
        self.calls.append(ip)
        item = self.plan.get(ip)
        if item is None:
            raise httpx.ConnectError("connection refused")
        if isinstance(item, Exception):
            raise item
        return _Resp(url, item)


def _fake_dns():
    """把 getaddrinfo 换成假表；返回恢复函数"""
    original = socket.getaddrinfo

    def fake(host, port, *a, **k):
        ip = _DNS.get(host)
        if ip is None:
            raise socket.gaierror("no such host")
        return [(socket.AF_INET, 1, 6, "", (ip, 0))]

    socket.getaddrinfo = fake
    return lambda: setattr(socket, "getaddrinfo", original)


def test_mirror_table_renders_and_stays_empty_for_unknown_hosts():
    urls = mirrors_for(_SRC)
    assert urls[0].startswith("https://ghproxy.net/https://raw.githubusercontent.com/")
    assert len(urls) >= 2 and all(u.startswith("https://") for u in urls)
    assert mirrors_for("https://registry.npmjs.org/react") == ["https://registry.npmmirror.com/react"]
    assert mirrors_for("https://pypi.org/simple/requests/") == ["https://mirrors.aliyun.com/pypi/simple/requests/"]
    assert mirrors_for("https://lite.duckduckgo.com/lite/?q=x") == []       # 没有镜像就别假装有
    assert mirrors_for("not a url") == []


async def test_official_wins_and_mirrors_are_never_touched():
    restore = _fake_dns()
    try:
        client = _Client({"1.1.1.1": 200})
        got = await wf.safe_get(client, _SRC, headers={})
    finally:
        restore()
    assert got.mirror is None and got.url == _SRC
    assert client.calls == ["1.1.1.1"]        # 官方通了就一次都不多试


async def test_official_failure_rotates_to_the_first_working_mirror():
    restore = _fake_dns()
    try:
        client = _Client({"1.1.1.1": httpx.ConnectError("dns poisoned"), "8.8.8.8": 200})
        got = await wf.safe_get(client, _SRC, headers={})
        assert got.mirror == "ghproxy.net"
        assert got.url.startswith("https://ghproxy.net/")
        assert got.as_result_extra()["via"] == "ghproxy.net"
        assert client.calls == ["1.1.1.1", "8.8.8.8"]

        # 第一个镜像返回 500 也要继续轮播（错误页不算成功）
        client2 = _Client({"1.1.1.1": httpx.ConnectError("x"), "8.8.8.8": 500, "8.8.4.4": 200})
        got2 = await wf.safe_get(client2, _SRC, headers={})
        assert got2.mirror == "gh-proxy.com"
        assert client2.calls == ["1.1.1.1", "8.8.8.8", "8.8.4.4"]

        # 官方自己返回 404 是"有答案"，不算失败，不该去轮播
        client3 = _Client({"1.1.1.1": 404})
        got3 = await wf.safe_get(client3, _SRC, headers={})
        assert got3.mirror is None and got3.response.status_code == 404
        assert client3.calls == ["1.1.1.1"]
    finally:
        restore()


async def test_all_mirrors_failing_raises_the_official_error():
    restore = _fake_dns()
    try:
        client = _Client({"1.1.1.1": httpx.ConnectError("dns poisoned")})   # 镜像全不通
        try:
            await wf.safe_get(client, _SRC, headers={})
            raise AssertionError("官方与镜像都不通时必须抛错，不能假装成功")
        except wf.BlockedFetch as e:
            assert "raw.githubusercontent.com" in str(e)
        assert client.calls[0] == "1.1.1.1" and len(client.calls) == 4       # 官方 + 3 个镜像
    finally:
        restore()


async def test_mirrors_can_be_switched_off():
    """要的就是这个站的原文时（allow_mirrors=False），失败就失败，不许换地方取"""
    restore = _fake_dns()
    try:
        client = _Client({"1.1.1.1": httpx.ConnectError("x"), "8.8.8.8": 200})
        try:
            await wf.safe_get(client, _SRC, headers={}, allow_mirrors=False)
            raise AssertionError("关掉镜像后不该有兜底")
        except wf.BlockedFetch:
            pass
        assert client.calls == ["1.1.1.1"]
    finally:
        restore()
