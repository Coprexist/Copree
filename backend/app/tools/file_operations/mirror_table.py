"""国内可达的镜像表 + 官方失败时的轮播顺序（**唯一来源**）。

口径："官网失败自动轮播镜像"。

四条硬规则（实现见 web_fetch.safe_get）：
1. **官方永远第一优先**——只有官方失败（DNS 被污染 / 连不上 / 超时）才轮到镜像，
   官方能通就绝不走第三方；
2. **只读**：只用于 GET，绝不代发 POST，也不带任何凭据（我们本来也不给这些站点带凭据）；
3. **说清楚**：用了镜像必须标出来（结果里的 `via`），内容来自第三方，AI 与用户都得知道；
4. **镜像自己也要过 SSRF 复检**：它走的是同一条取数管线（挑地址 / 钉连接 / 逐跳复检重定向）。

表里**只登记实测过的**镜像（2026-09-16 逐条验证：状态码 + 真内容），不铺一张没人验证过的表。
模板占位符：`{url}` = 完整原始 URL（前缀代理风格）、`{path}` = path + query、`{host}` = 原主机名。
"""
from __future__ import annotations

from urllib.parse import urlsplit

# GitHub 系：前缀代理（把原始 URL 整个接在后面），三个互为备份
_GH_PROXIES = (
    "https://ghproxy.net/{url}",
    "https://gh-proxy.com/{url}",
    "https://ghfast.top/{url}",
)

MIRRORS: dict[str, tuple[str, ...]] = {
    # ── GitHub：raw 文件 / release 包 / archive 压缩包 / gist ──
    "raw.githubusercontent.com": _GH_PROXIES,
    "github.com": _GH_PROXIES,
    "codeload.github.com": ("https://ghproxy.net/{url}",),
    "objects.githubusercontent.com": ("https://ghproxy.net/{url}",),
    "gist.githubusercontent.com": ("https://ghproxy.net/{url}",),
    # ── 包仓库：国内官方镜像（比代理更稳，也更快）──
    "registry.npmjs.org": ("https://registry.npmmirror.com/{path}",),
    "pypi.org": ("https://mirrors.aliyun.com/pypi/{path}",),
    # ── 模型仓库 ──
    "huggingface.co": ("https://hf-mirror.com/{path}",),
}

# 备注：cdn.jsdelivr.net 也能代理 GitHub raw / npm，但它要拆 owner/repo/ref 重排路径
# （与"前缀代理"不是同一种写法），为了只有一套机制，没有进表。


def mirrors_for(url: str) -> list[str]:
    """该 URL 的镜像候选（已渲染好，按表里的顺序）；没有镜像就返回空表。

    **官方地址不在返回值里**——调用方一律先试官方，失败才用这里。
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return []
    host = (parts.hostname or "").lower()
    templates = MIRRORS.get(host)
    if not templates:
        return []
    # 模板自己写斜杠，这里只给"路径本体"（避免出现 //）
    path = (parts.path or "/").lstrip("/")
    if parts.query:
        path = f"{path}?{parts.query}"
    return [t.format(url=url, path=path, host=host) for t in templates]
