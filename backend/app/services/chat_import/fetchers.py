"""
抓取能力注册表 —— 插件挂进来的那一格。

核心（本模块所属的平台）**不认识任何平台域名**：
能不能抓、怎么抓，全由插件声明。没装插件时 find() 返回 None，
接口据此回「请改用粘贴或上传文件」，而不是自己偷偷发一个请求。

这是「拉取不合它们的规」这条风险的隔离墙：卸载插件，网络能力就跟着消失，
平台代码里不留一条出站请求。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

FetcherFn = Callable[[str], Awaitable[str]]
CanFn = Callable[[str], bool]


@dataclass(frozen=True)
class Fetcher:
    platform: str          # deepseek / chatgpt …
    label: str             # 展示名
    can: CanFn             # url -> 认不认
    fetch: FetcherFn       # url -> 原始 payload


_fetchers: list[Fetcher] = []


def register(fetcher: Fetcher) -> None:
    global _fetchers
    _fetchers = [f for f in _fetchers if f.platform != fetcher.platform] + [fetcher]


def available() -> list[dict]:
    return [{"platform": f.platform, "label": f.label} for f in _fetchers]


def find(url: str) -> Fetcher | None:
    for f in _fetchers:
        try:
            if f.can(url):
                return f
        except Exception:
            continue
    return None
