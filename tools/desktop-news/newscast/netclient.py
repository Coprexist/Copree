"""HTTP 客户端：只用标准库 urllib，带 UA、压缩、重试与编码嗅探。

为什么要自己写一层：目标是「目标机零依赖」，装 requests 就走不通了；而 urllib 默认
UA 会被大量国内站点直接拒绝，编码也不会按 meta 声明嗅探，国内站点回 GBK 会变乱码。
"""

from __future__ import annotations

import gzip
import io
import json
import random
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from typing import Any

DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# 先按标准库默认校验走（Windows 上走系统证书库），失败且允许时再降级重试一次
_INSECURE_CTX: ssl.SSLContext | None = None


class FetchError(RuntimeError):
    """抓取失败：网络错误、超时、HTTP 非 2xx 都归到这里，调用方只需关心「这条源挂了」。"""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def _insecure_context() -> ssl.SSLContext:
    global _INSECURE_CTX
    if _INSECURE_CTX is None:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        _INSECURE_CTX = ctx
    return _INSECURE_CTX


def _decode_charset(raw: bytes, content_type: str = "") -> str:
    candidates: list[str] = []
    if content_type:
        for part in content_type.split(";")[1:]:
            if "charset=" in part.lower():
                candidates.append(part.split("=", 1)[1].strip().strip('"\''))
    head = raw[:2048].decode("ascii", "ignore").lower()
    for marker in ('charset="', "charset='", "charset="):
        idx = head.find(marker)
        if idx != -1:
            val = head[idx + len(marker):].split('"')[0].split("'")[0].split(">")[0].split()[0]
            if val:
                candidates.append(val.strip())
            break
    xml_decl = raw[:200].decode("ascii", "ignore").lower()
    if "encoding=" in xml_decl:
        candidates.append(xml_decl.split("encoding=", 1)[1].split("?")[0].strip("\"' "))

    candidates += ["utf-8", "gb18030", "big5"]
    for enc in candidates:
        if not enc:
            continue
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace")


def fetch_bytes(
    url: str,
    timeout: float = 15.0,
    retries: int = 2,
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    method: str | None = None,
    allow_insecure_fallback: bool = False,
    proxy: str | None = None,
) -> tuple[bytes, str]:
    """返回 (原始字节, content-type)。失败抛 FetchError。"""
    if not url:
        raise FetchError("空 URL")
    hdrs = {
        "User-Agent": DEFAULT_UA,
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "close",
    }
    if headers:
        hdrs.update(headers)

    last_err: Exception | None = None
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    else:
        handlers.append(urllib.request.ProxyHandler())  # 尊重系统/环境变量代理设置

    for attempt in range(max(1, retries + 1)):
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            opener = urllib.request.build_opener(*handlers)
            with opener.open(req, timeout=timeout) as resp:
                raw = resp.read()
                ctype = resp.headers.get("Content-Type", "") or ""
                enc = (resp.headers.get("Content-Encoding") or "").lower()
                if "gzip" in enc:
                    try:
                        raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
                    except OSError:
                        pass
                elif "deflate" in enc:
                    try:
                        raw = zlib.decompress(raw)
                    except zlib.error:
                        try:
                            raw = zlib.decompress(raw, -zlib.MAX_WBITS)
                        except zlib.error:
                            pass
                return raw, ctype
        except urllib.error.HTTPError as exc:
            last_err = exc
            # 4xx 多为源站改版/封禁，重试无意义；429 与 5xx 值得退避重试
            if exc.code < 500 and exc.code != 429:
                raise FetchError(f"HTTP {exc.code} {exc.reason}", status=exc.code) from exc
        except ssl.SSLError as exc:
            last_err = exc
            if allow_insecure_fallback and attempt == 0:
                handlers = [urllib.request.HTTPSHandler(context=_insecure_context())]
                continue
        except urllib.error.URLError as exc:
            last_err = exc
        except (TimeoutError, OSError) as exc:
            last_err = exc

        if attempt < retries:
            time.sleep(min(4.0, 0.6 * (2 ** attempt)) + random.uniform(0, 0.4))

    raise FetchError(f"{type(last_err).__name__}: {last_err}")


def fetch_text(url: str, timeout: float = 15.0, retries: int = 2,
               headers: dict[str, str] | None = None,
               allow_insecure_fallback: bool = False, proxy: str | None = None) -> str:
    raw, ctype = fetch_bytes(url, timeout=timeout, retries=retries, headers=headers,
                            allow_insecure_fallback=allow_insecure_fallback, proxy=proxy)
    return _decode_charset(raw, ctype)


def fetch_json(url: str, timeout: float = 15.0, retries: int = 2,
               headers: dict[str, str] | None = None,
               allow_insecure_fallback: bool = False, proxy: str | None = None) -> Any:
    text = fetch_text(url, timeout=timeout, retries=retries, headers=headers,
                      allow_insecure_fallback=allow_insecure_fallback, proxy=proxy)
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # 少数接口会包一层 JSONP（callback({...})）
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start:end + 1])
        raise


def post_json(url: str, payload: dict, timeout: float = 60.0, retries: int = 1,
              headers: dict[str, str] | None = None,
              allow_insecure_fallback: bool = False) -> Any:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    hdrs = {"Content-Type": "application/json; charset=utf-8"}
    if headers:
        hdrs.update(headers)
    raw, ctype = fetch_bytes(url, timeout=timeout, retries=retries, headers=hdrs, data=body,
                            method="POST", allow_insecure_fallback=allow_insecure_fallback)
    return json.loads(_decode_charset(raw, ctype))


def quote(text: str) -> str:
    return urllib.parse.quote(text)
