"""
版本更新检查 —— 「本站跑的是不是最新版」的唯一来源。

三个使用面共用这一份结论，各自只负责展示：
- 管理面板「通用」→ 版本与更新卡片（docker / 源码部署看这里，卡片给对应的升级方式）；
- 桌面版顶部提示条（点一下直接下安装包）与启动器页脚（还没打开浏览器就能看见）；
- \`/api/system/update-status\` 接口本身。

四条要求决定了它长成现在这样：

1. **出站请求只发生在服务端**，前端永远只读本地接口。让浏览器直连 GitHub 的话，
   境内网络下会出现「同一个人有时看得到更新、有时看不到」，而且每个客户端各打一次 API。
2. **上游一分钟内最多打一次**：GitHub 未认证 API 是 60 次/小时/IP。三层挡在同一条路上——
   TTL 缓存（正常情况根本不出网）、单飞（并发未命中只打一次）、手动检查节流（狂点刷新也安全）。
3. **失败不抛错、不告警、也不丢上一次的结论**：离线/内网实例每次都会失败，界面必须能安静地
   说「无法检查」；同时不能用一次失败把已经知道的「有新版本」冲掉——缓存分「数据」与「最近一次
   尝试结果」两半，失败只动后者。
4. **上游形状可换**：默认 GitHub Releases API；企业内网或镜像站把 \`UPDATE_CHECK_URL\` 指到任意
   JSON 也能用（两种形状由 \`parse_release\` 归一化），自建发布渠道不必改代码。

刻意留的口子：\`reset()\` 只给测试用；模块级缓存是进程内的，多 worker 部署每个进程各算一次，
换来的是「不因为一个展示性功能给数据库加一张表」。
"""
from __future__ import annotations

import asyncio
import logging
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from app.config import settings
from app.utils.pure.version import compare_versions, is_newer, normalize_version, parse_version

logger = logging.getLogger(__name__)

DEFAULT_URL = "https://api.github.com/repos/Coprexist/Copree-Releases/releases?per_page=20"
# 渠道：stable 只认正式版，beta 连预发布一起认
CHANNELS = ("stable", "beta")
# 「立即检查」的节流窗口：正常路径由 TTL 兜住，这个只管手动刷新，
# 免得 20 个人同时点、或者有人拿脚本刷，把未认证额度打光。
MIN_FORCE_INTERVAL = 120.0
# 回给前端的更新说明上限：卡片里只放摘要，全文在 GitHub 页面上看
NOTES_LIMIT = 1500
# 各平台对应的安装包后缀：桌面版据此挑「这台机器的那个包」
ASSET_SUFFIX = {"win32": ".exe", "darwin": ".dmg", "linux": ".tar.gz"}


@dataclass(frozen=True)
class Release:
    """一次成功解析的上游发布信息（与上游形状无关的中间形状）。"""

    version: str            # 归一化后的版本号（无 v 前缀）
    tag: str                # 上游原始 tag，用于展示/链接兜底
    url: str                # 发布页（给人看的）
    published_at: str       # ISO 时间，取不到就空
    notes: str              # 更新说明纯文本（已截断）
    assets: tuple[dict, ...]  # [{"name","url","size"}]
    prerelease: bool = False  # 预发布：来自上游标记**或**版本号自身的后缀，见 parse_release


@dataclass
class _Cache:
    """一半是数据、一半是最近一次尝试的结果——失败只覆盖后一半。"""

    release: Release | None = None
    fetched_at: float = 0.0     # monotonic，成功时刻
    fetched_wall: float = 0.0   # epoch，回给前端的 checked_at
    attempted_at: float = 0.0   # monotonic，最近一次尝试（成功或失败）
    error: str = ""             # 空 = 最近一次尝试成功


_cache = _Cache()
_inflight: asyncio.Future | None = None


def reset() -> None:
    """清空进程内缓存（测试用；生产代码没有任何调用点）。"""
    global _inflight
    _cache.release = None
    _cache.fetched_at = 0.0
    _cache.fetched_wall = 0.0
    _cache.attempted_at = 0.0
    _cache.error = ""
    _inflight = None


# ── 纯解析部分（无网络、可单测） ──

def _text(value: object, limit: int = NOTES_LIMIT) -> str:
    """上游字符串 → 展示用纯文本：去 \r、压缩连续空行、截断。"""
    if not isinstance(value, str):
        return ""
    text = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    if len(text) > limit:
        text = text[:limit].rstrip() + "…"
    return text


def _assets(payload_assets: object) -> tuple[dict, ...]:
    """把两种形状的 assets 归一化成 [{"name","url","size"}]，认不出的条目直接丢。"""
    if not isinstance(payload_assets, list):
        return ()
    out: list[dict] = []
    for item in payload_assets:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("filename") or ""
        url = item.get("browser_download_url") or item.get("url") or item.get("download_url") or ""
        if not name or not url:
            continue
        size = item.get("size")
        out.append({"name": str(name), "url": str(url), "size": size if isinstance(size, int) else 0})
    return tuple(out)


def parse_release(payload: object) -> Release | None:
    """上游 JSON → Release。认不出返回 None（调用方按「响应不可用」处理，不猜）。

    认两种形状：
    - GitHub Releases API：{tag_name, html_url, published_at, body, assets[]}
    - 自建镜像的简单形状：{version|latest, url|html_url, notes|body, published_at|date, assets[]}
    """
    if not isinstance(payload, dict):
        return None
    tag = payload.get("tag_name") or payload.get("version") or payload.get("latest") or ""
    version = normalize_version(tag)
    if not version or not any(ch.isdigit() for ch in version):
        return None
    url = payload.get("html_url") or payload.get("url") or payload.get("release_url") or ""
    published = payload.get("published_at") or payload.get("created_at") or payload.get("date") or ""
    notes = _text(payload.get("body") if payload.get("body") is not None else payload.get("notes"))
    # 预发布的两个来源缺一不可：CI 建 Release 时并没有打 prerelease 标记（见发布仓
    # .github/workflows/build.yml），只看标记的话 v0.5.0-beta.1 会被当成正式版推给所有人；
    # 反过来只看版本号也不行——镜像可能用 1.2.3 这种号发预发布，只能靠标记表达。
    parsed_tag = parse_version(tag)
    prerelease = (
        payload.get("prerelease") is True
        or payload.get("is_prerelease") is True
        or bool(parsed_tag is not None and parsed_tag.pre)
    )
    return Release(
        version=version,
        tag=str(tag),
        url=str(url),
        published_at=str(published),
        notes=notes,
        assets=_assets(payload.get("assets")),
        prerelease=prerelease,
    )


def _release_candidates(payload: object) -> list[dict]:
    """上游响应 → 发布条目列表。单个对象（/releases/latest 或镜像）也当作只有一条。"""
    if isinstance(payload, dict):
        return [payload]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def select_release(payload: object, channel: str = "stable") -> Release | None:
    """从上游响应里挑出本渠道该用的那一条：跳过草稿，stable 跳过预发布，其余取**版本最高**的。

    为什么按版本取而不是按发布日期取：热修分支可能后建却版本更低（0.4.15 的补丁在
    0.5.0 预览之后才发布），"最新创建的"不等于"最新的"，只有版本号能回答用户真正想问的。
    """
    want_pre = channel == "beta"
    best: Release | None = None
    for item in _release_candidates(payload):
        if item.get("draft") is True:
            continue
        parsed = parse_release(item)
        if parsed is None:
            continue
        if not want_pre and parsed.prerelease:
            continue
        if best is None or compare_versions(parsed.version, best.version) > 0:
            best = parsed
    return best


def pick_download(assets: tuple[dict, ...], platform: str = "") -> str:
    """挑出本机平台对应的安装包直链；没有就返回空串（前端退化成「去发布页」）。"""
    suffix = ASSET_SUFFIX.get(platform or sys.platform)
    if not suffix:
        return ""
    for asset in assets:
        if asset["name"].lower().endswith(suffix):
            return asset["url"]
    return ""


# ── 网络部分 ──

async def _fetch(url: str, timeout: float) -> tuple[Release | None, str]:
    """取一次上游。返回 (Release|None, 错误码)；错误码是给界面分类用的，不是给人读的长句。"""
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": f"copree/{settings.app_version}",
    }
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
    except httpx.TimeoutException:
        return None, "timeout"
    except Exception as exc:                      # 网络栈的异常类型太多，一律归为「不通」
        logger.debug("更新检查请求失败: %s", exc)
        return None, "unreachable"

    if resp.status_code == 404:
        return None, "not_found"
    if resp.status_code in (403, 429):
        return None, "rate_limited"
    if resp.status_code >= 400:
        return None, f"http_{resp.status_code}"
    try:
        payload = resp.json()
    except Exception:
        return None, "bad_response"
    if not _release_candidates(payload):
        return None, "bad_response"
    release = select_release(payload, settings.update_check_channel)
    if release is None:
        # 上游通了、但没有本渠道可用的发布（比如 stable 渠道只看到预发布）
        return None, "no_release"
    return release, ""


async def _refresh() -> None:
    """打一次上游并更新缓存。成功才覆盖「数据」，失败只覆盖「最近一次尝试」。"""
    release, error = await _fetch(settings.update_check_url or DEFAULT_URL,
                                  float(settings.update_check_timeout_seconds))
    now = time.monotonic()
    # 失败也把 attempted_at 推到当下：否则离线实例每个请求都会再打一次上游
    _cache.attempted_at = now
    _cache.error = error
    if release is not None:
        _cache.release = release
        _cache.fetched_at = now
        _cache.fetched_wall = time.time()
        logger.info("更新检查：上游最新版本 %s（当前 %s）", release.version, settings.app_version)
    else:
        logger.warning("更新检查失败：%s", error)


def _iso(epoch: float) -> str:
    if not epoch:
        return ""
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _payload(*, cached: bool, throttled: bool = False) -> dict:
    """当前缓存 → 接口响应。纯读，不改缓存。"""
    release = _cache.release
    current = normalize_version(settings.app_version)
    stale = release is not None and (_cache.error != "" or
                                     time.monotonic() - _cache.fetched_at > settings.update_check_ttl_seconds)
    payload = {
        "current": current,
        "distribution": settings.app_distribution,
        "channel": settings.update_check_channel,
        "enabled": settings.update_check_enabled,
        "ok": release is not None,
        "latest": release.version if release else "",
        "has_update": bool(release and is_newer(release.version, current)),
        "release_url": release.url if release else "",
        "download_url": pick_download(release.assets) if release and settings.app_distribution == "desktop" else "",
        "published_at": release.published_at if release else "",
        "notes": release.notes if release else "",
        "assets": [dict(a) for a in release.assets] if release else [],
        "checked_at": _iso(_cache.fetched_wall),
        "cached": cached,
        "stale": stale,
        "throttled": throttled,
        "error": _cache.error,
    }
    return payload


async def get_update_status(*, force: bool = False) -> dict:
    """接口的唯一入口：能答就答缓存，答不了就去问一次上游，问不到也照样答。

    force=True 是用户点「立即检查」：受 MIN_FORCE_INTERVAL 节流，窗口内直接回缓存并标 throttled。
    """
    current = normalize_version(settings.app_version)
    if not settings.update_check_enabled:
        return {
            "current": current,
            "distribution": settings.app_distribution,
            "channel": settings.update_check_channel,
            "enabled": False,
            "ok": False,
            "latest": "", "has_update": False, "release_url": "", "download_url": "",
            "published_at": "", "notes": "", "assets": [],
            "checked_at": "", "cached": False, "stale": False, "throttled": False,
            "error": "disabled",
        }

    now = time.monotonic()
    ttl = max(60.0, float(settings.update_check_ttl_seconds))
    fresh = _cache.release is not None and (now - _cache.fetched_at) < ttl
    # 失败也挡一个 ttl：离线实例上每个请求都去试一次上游，等于把「检查更新」变成
    # 一个每次超时 6 秒的轮询器。要立刻重试就只有手动检查（force）这一条路。
    backing_off = bool(_cache.error) and bool(_cache.attempted_at) and (now - _cache.attempted_at) < ttl
    throttled = bool(force and _cache.attempted_at and (now - _cache.attempted_at) < MIN_FORCE_INTERVAL)
    need = not throttled and (force or (not fresh and not backing_off))

    global _inflight
    if need:
        task = _inflight
        if task is None or task.done():
            # 从「判断需要刷新」到「登记在飞任务」之间没有 await：同一事件循环里天然互斥，
            # 并发未命中只会产生一个上游请求（也就不用锁）
            task = asyncio.ensure_future(_refresh())
            _inflight = task
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            raise
        except Exception:                     # _refresh 自己吞异常；这里只是不让展示功能拖垮请求
            logger.exception("更新检查异常")
    return _payload(cached=not need, throttled=throttled)
