"""
聊天记录抓取插件 —— 整个导入链路里**唯一**会对外发请求的地方。

为什么隔离在这里（用户的判断，写下来免得以后被"顺手挪回核心"）：

- 我们拉的是各平台**未公开文档化**的第一方接口（DeepSeek 的
  `/api/v0/share/content`），性质从"看网页"变成"调内部接口"，严格讲违反 ToS；
- 出口 IP 与主业务共用，万一被平台风控，连带的不是导入功能而是这台机器上其他业务；
- 平台随时可能加签名 / 改字段 / 加限流，这段代码**一定会坏**，坏了要能整块摘掉。

所以这里只有「URL → 原始 payload」这一件事：解析在核心（离线、可测），
落库在核心，限频与失败口径也在这里——失败就停，不重试、不换 UA、不绕验证。

已有能力：
- DeepSeek 公开分享页：`https://chat.deepseek.com/share/<id>`

没有能力时（用户贴了别的平台链接），核心会回一句「没装能抓这个链接的插件」，
并建议改用粘贴或上传官方导出的文件——不会静默失败，也不会自己去试。
"""
from __future__ import annotations

import asyncio
import logging
import re
import time

import httpx

from app.services.chat_import.fetchers import Fetcher, register

logger = logging.getLogger(__name__)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# 一次抓取的超时。分享页本身很大（长对话几百 KB），30s 是够的；
# 超时就当失败——重试只会让对方更容易把我们当爬虫。
TIMEOUT = 30.0
# 同一个进程里两次抓取的最小间隔：用户手快连点两下不至于连打对方接口。
MIN_INTERVAL = 1.5

_SHARE_RE = re.compile(r"chat\.deepseek\.com/(?:share|s)/([A-Za-z0-9]+)")

_lock = asyncio.Lock()
_last_at = 0.0


def _can(url: str) -> bool:
    return bool(_SHARE_RE.search(url or ""))


async def _throttle() -> None:
    global _last_at
    async with _lock:
        wait = MIN_INTERVAL - (time.monotonic() - _last_at)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_at = time.monotonic()


async def _fetch_deepseek(url: str) -> str:
    m = _SHARE_RE.search(url)
    if not m:
        raise ValueError("这不是 DeepSeek 的分享链接")
    share_id = m.group(1)

    await _throttle()
    api = "https://chat.deepseek.com/api/v0/share/content?share_id=" + share_id
    headers = {
        "User-Agent": UA,          # 裸 UA 会被 WAF 回 429；这是浏览器的基本姿态，不是伪装
        "Accept": "*/*",
        "Referer": "https://chat.deepseek.com/share/" + share_id,
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False) as client:
        resp = await client.get(api, headers=headers)
    if resp.status_code == 429:
        raise RuntimeError("对方限流了（429），过一会儿再试，或改用粘贴/上传")
    if resp.status_code != 200:
        raise RuntimeError(f"对方返回 HTTP {resp.status_code}")

    text = resp.text
    # biz_code 非 0 = 接口拒了（分享被删 / 未公开 / 需要登录），原样报给用户
    if '"biz_code"' in text and '"biz_code":0' not in text.replace(" ", ""):
        raise RuntimeError("这个分享打不开（可能已删除或未公开）")
    return text


register(Fetcher(
    platform="deepseek",
    label="DeepSeek 分享",
    can=_can,
    fetch=_fetch_deepseek,
))
