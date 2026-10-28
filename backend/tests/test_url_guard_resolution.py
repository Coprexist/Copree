"""出站守卫的域名解析：判定口径、缓存、以及"解析不许占住事件循环"。

2026-10-28 实测：本机解析不到 example.com 要 16 秒。同步解析摆在 async 路径上有两层代价——
全量测试 124 秒里 30 多秒在等 DNS，线上一次"测试连接"也能把 worker 停十几秒。所以：

- 判定口径只有一份（`classify_address` / `_judge`），同步与 async 两个入口不许给出不同答案；
- 解析结果带 60 秒缓存，失败也缓存（那十几秒不该每次点击重付一遍）；
- async 入口把解析丢到线程里：解析在等的时候，事件循环还得能跑别的协程。
"""
import asyncio
import socket
import time

import pytest

pytestmark = pytest.mark.anyio

_PUBLIC = [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 0))]
_INTERNAL = [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("192.168.1.9", 0))]


def _resolver(seen: list[str], answers=None, *, delay: float = 0.0):
    def resolve(host, port, *args, **kwargs):
        seen.append(str(host))
        if delay:
            time.sleep(delay)
        return answers if answers is not None else _PUBLIC
    return resolve


async def test_sync_and_async_entries_agree():
    """两个入口同一套口径：本机/内网/公网，判出来的结论必须一致"""
    from app.utils.pure import url_guard

    url_guard.clear_resolution_cache()
    for url, expected in (
        ("http://localhost:11434", True),
        ("http://127.0.0.1:8000", True),
        ("http://192.168.1.5:8000", True),
        ("https://api.deepseek.com", False),
    ):
        assert url_guard.is_private_target(url) is expected, url
        assert await url_guard.is_private_target_async(url) is expected, url


def test_resolution_is_cached_and_cached_failures_too():
    """同一主机在缓存期内只问一次 DNS；解析失败同样缓存（否则每次点击都要等一遍）"""
    from app.utils.pure import url_guard

    original = socket.getaddrinfo
    seen: list[str] = []
    socket.getaddrinfo = _resolver(seen)
    url_guard.clear_resolution_cache()
    try:
        assert url_guard.is_private_target("https://api.example.net") is False
        assert url_guard.is_private_target("https://api.example.net") is False
        assert seen == ["api.example.net"], seen

        url_guard.clear_resolution_cache()
        url_guard.is_private_target("https://api.example.net")
        assert len(seen) == 2, seen

        def failing(host, port, *args, **kwargs):
            seen.append(str(host))
            raise socket.gaierror("no such host")

        socket.getaddrinfo = failing
        url_guard.clear_resolution_cache()
        assert url_guard.is_private_target("https://broken.example.net") is False
        assert url_guard.is_private_target("https://broken.example.net") is False
        assert seen.count("broken.example.net") == 1, seen
    finally:
        socket.getaddrinfo = original
        url_guard.clear_resolution_cache()


async def test_async_entry_does_not_stop_the_loop_while_dns_is_slow():
    """解析慢的时候事件循环还得转：这正是同步版把整个 worker 停住的形状"""
    from app.utils.pure import url_guard

    original = socket.getaddrinfo
    seen: list[str] = []
    socket.getaddrinfo = _resolver(seen, delay=0.3)
    url_guard.clear_resolution_cache()
    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0.02)

    task = asyncio.create_task(ticker())
    try:
        started = time.perf_counter()
        assert await url_guard.is_private_target_async("https://slow.example.net") is False
        elapsed = time.perf_counter() - started
    finally:
        task.cancel()
        socket.getaddrinfo = original
        url_guard.clear_resolution_cache()

    assert elapsed >= 0.25, elapsed
    assert ticks >= 3, f"解析期间事件循环被挡住了：只转了 {ticks} 下"
