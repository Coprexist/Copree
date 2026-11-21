"""通道侧的媒体口径 —— 入站取图这一件事，两条 QQ 通道共用

平台（消息表、附件表、多模态上下文）只认 `{file_id, path, name, size, mime_type}`；
"从通道事件里拿到的那条 URL 怎么安全地取字节"是**通道侧的事实**，所以放在这里，
插件各自只负责把字节登记成附件。

为什么要白名单：事件里的 url 由协议端给，但插件不该变成任意 URL 抓取器
（一条伪造事件就能让服务器去打内网地址）。
"""
from __future__ import annotations

import os

import httpx

from app.utils.mime import IMAGE_MIMES

# 入站取图的超时与体积上限：通道图片通常几百 KB，超过 15MB 的当异常
DOWNLOAD_TIMEOUT = 20.0
DOWNLOAD_MAX_BYTES = 15 * 1024 * 1024

# 只认通道自己的多媒体域；调用方需要更多域时通过 allowed_hosts 显式追加
ALLOWED_HOSTS = ("multimedia.nt.qq.com.cn", "gchat.qpic.cn", "qq.com")

_EXT_MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp",
}


def host_allowed(url: str, allowed: tuple[str, ...] = ALLOWED_HOSTS) -> bool:
    try:
        host = (httpx.URL(url).host or "").lower()
    except Exception:
        return False
    return bool(host) and any(host == h or host.endswith("." + h) for h in allowed)


async def download(
    url: str, *, allowed: tuple[str, ...] = ALLOWED_HOSTS, logger=None,
) -> tuple[bytes, str] | None:
    """取一张通道图片 -> (字节, mime)；取不到返回 None（调用方保留正文里的占位）。

    失败一律降级而不是抛：一条图抓不到不该挡住整条消息落库。
    """
    if not host_allowed(url, allowed):
        if logger is not None:
            logger.warning("通道入站图片地址不在允许的域，跳过：%s", url.split("?")[0][:120])
        return None
    try:
        async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT, follow_redirects=True) as http:
            async with http.stream("GET", url) as res:
                if res.status_code >= 400:
                    if logger is not None:
                        logger.warning("通道入站图片下载失败（HTTP %s）", res.status_code)
                    return None
                buf = bytearray()
                async for chunk in res.aiter_bytes():
                    buf.extend(chunk)
                    if len(buf) > DOWNLOAD_MAX_BYTES:
                        if logger is not None:
                            logger.warning("通道入站图片超过 %s MB，跳过", DOWNLOAD_MAX_BYTES // 1024 // 1024)
                        return None
                mime = str(res.headers.get("content-type") or "").split(";")[0].strip().lower()
    except (httpx.HTTPError, OSError) as e:
        if logger is not None:
            logger.warning("通道入站图片下载失败：%s: %s", type(e).__name__, e)
        return None
    if not buf:
        return None
    if mime not in IMAGE_MIMES:
        mime = _EXT_MIME.get(os.path.splitext(httpx.URL(url).path or "")[1].lower(), "image/jpeg")
    return bytes(buf), mime
