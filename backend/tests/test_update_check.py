"""更新检查服务的行为契约：缓存、单飞、节流、失败降级。

这套用例不打网络：内部取数函数一律被替换成假实现。守的是四条容易被改坏的约定——
1. TTL 内的第二次调用不再出网（GitHub 未认证额度只有 60 次/小时）；
2. 并发未命中只产生一次请求（单飞）；
3. 「立即检查」在节流窗口内直接回缓存（狂点刷新也安全）；
4. 失败不清空已经知道的结论，只标记 stale（离线实例不能把红点刷没）。
"""
from __future__ import annotations

import asyncio
import time

from app.config import settings
from app.services.infrastructure import update_check as uc


def _github_payload(version: str = "v0.4.16") -> dict:
    return {
        "tag_name": version,
        "html_url": f"https://github.com/Coprexist/Copree-Releases/releases/tag/{version}",
        "published_at": "2026-10-28T10:00:00Z",
        "body": "## 修复\r\n\r\n- 某个东西\r\n\r\n\r\n- 另一个",
        "assets": [
            {"name": "AIsChat-Installer.exe", "browser_download_url": "https://x/AIsChat-Installer.exe", "size": 1},
            {"name": "AIsChat.dmg", "browser_download_url": "https://x/AIsChat.dmg", "size": 2},
            {"name": "AIsChat-Linux.tar.gz", "browser_download_url": "https://x/AIsChat-Linux.tar.gz", "size": 3},
        ],
    }


class _patched_settings:
    """临时改 settings 字段（本运行器没有 monkeypatch 夹具）。"""

    def __init__(self, **kwargs):
        self._kwargs = kwargs
        self._saved: dict = {}

    def __enter__(self):
        for key, value in self._kwargs.items():
            self._saved[key] = getattr(settings, key)
            setattr(settings, key, value)
        return self

    def __exit__(self, *_exc):
        for key, value in self._saved.items():
            setattr(settings, key, value)
        return False


def _fake_fetch(result, calls: list):
    async def fetch(url: str, timeout: float):
        calls.append(url)
        return result

    return fetch


def test_parse_github_release():
    release = uc.parse_release(_github_payload())
    assert release is not None
    assert release.version == "0.4.16"          # 前缀 v 已归一化
    assert release.tag == "v0.4.16"
    assert release.url.endswith("/v0.4.16")
    assert release.published_at == "2026-10-28T10:00:00Z"
    assert "\r" not in release.notes             # CRLF 已归一化
    assert "\n\n\n" not in release.notes         # 连续空行已压缩
    assert len(release.assets) == 3


def test_parse_simple_mirror_shape():
    """自建镜像只给最小字段也能用——企业内网把 UPDATE_CHECK_URL 指过来即可。"""
    release = uc.parse_release({
        "version": "v0.5.0",
        "url": "https://mirror.example.com/copree/0.5.0",
        "notes": "首个 0.5 版本",
        "assets": [{"name": "Copree.zip", "url": "https://mirror.example.com/copree.zip"}],
    })
    assert release is not None
    assert release.version == "0.5.0"
    assert release.notes == "首个 0.5 版本"
    assert release.assets[0]["size"] == 0         # 上游没给 size 也不许是 None


def test_parse_rejects_unusable_payloads():
    # GitHub 在仓库不存在/私有库时返回 {"message": "Not Found"}
    assert uc.parse_release({"message": "Not Found"}) is None
    assert uc.parse_release({"tag_name": "latest"}) is None      # 没有数字，认不出
    assert uc.parse_release([]) is None
    assert uc.parse_release("0.4.16") is None
    assert uc.parse_release(None) is None


def test_notes_are_truncated():
    long_body = "x" * (uc.NOTES_LIMIT + 500)
    release = uc.parse_release({"tag_name": "v1.0.0", "body": long_body})
    assert release is not None
    assert len(release.notes) == uc.NOTES_LIMIT + 1    # 截断后补一个省略号
    assert release.notes.endswith("…")


def test_pick_download_matches_platform():
    assets = uc.parse_release(_github_payload()).assets
    assert uc.pick_download(assets, "win32") == "https://x/AIsChat-Installer.exe"
    assert uc.pick_download(assets, "darwin") == "https://x/AIsChat.dmg"
    assert uc.pick_download(assets, "linux") == "https://x/AIsChat-Linux.tar.gz"
    assert uc.pick_download((), "win32") == ""
    assert uc.pick_download(assets, "plan9") == ""


async def test_disabled_short_circuits_without_network():
    calls: list = []
    original = uc._fetch
    uc.reset()
    with _patched_settings(update_check_enabled=False):
        uc._fetch = _fake_fetch((uc.parse_release(_github_payload()), ""), calls)
        try:
            status = await uc.get_update_status()
        finally:
            uc._fetch = original
    assert calls == []
    assert status["enabled"] is False
    assert status["error"] == "disabled"
    assert status["has_update"] is False


async def test_ttl_cache_avoids_second_request():
    calls: list = []
    original = uc._fetch
    uc.reset()
    uc._fetch = _fake_fetch((uc.parse_release(_github_payload()), ""), calls)
    try:
        first = await uc.get_update_status()
        second = await uc.get_update_status()
    finally:
        uc._fetch = original
        uc.reset()
    assert len(calls) == 1
    assert first["cached"] is False and second["cached"] is True
    assert second["latest"] == "0.4.16"
    assert second["has_update"] is True
    assert second["checked_at"].endswith("Z")


async def test_concurrent_misses_single_flight():
    calls: list = []

    async def slow_fetch(url: str, timeout: float):
        calls.append(url)
        await asyncio.sleep(0.02)
        return uc.parse_release(_github_payload()), ""

    original = uc._fetch
    uc.reset()
    uc._fetch = slow_fetch
    try:
        results = await asyncio.gather(*[uc.get_update_status() for _ in range(6)])
    finally:
        uc._fetch = original
        uc.reset()
    assert len(calls) == 1, "并发未命中只允许打一次上游"
    assert all(r["latest"] == "0.4.16" for r in results)


async def test_force_is_throttled_inside_the_window():
    calls: list = []
    original = uc._fetch
    uc.reset()
    uc._fetch = _fake_fetch((uc.parse_release(_github_payload()), ""), calls)
    try:
        await uc.get_update_status()
        forced = await uc.get_update_status(force=True)     # 刚检查过，节流窗口还没过
    finally:
        uc._fetch = original
        uc.reset()
    assert len(calls) == 1
    assert forced["throttled"] is True
    assert forced["cached"] is True


async def test_force_after_window_hits_upstream_again():
    calls: list = []
    original = uc._fetch
    uc.reset()
    uc._fetch = _fake_fetch((uc.parse_release(_github_payload()), ""), calls)
    try:
        await uc.get_update_status()
        uc._cache.attempted_at = time.monotonic() - uc.MIN_FORCE_INTERVAL - 1
        forced = await uc.get_update_status(force=True)
    finally:
        uc._fetch = original
        uc.reset()
    assert len(calls) == 2
    assert forced["throttled"] is False


async def test_failure_keeps_last_known_release():
    original = uc._fetch
    uc.reset()
    try:
        uc._fetch = _fake_fetch((uc.parse_release(_github_payload()), ""), [])
        await uc.get_update_status()
        # 网络断了：结论要留着，只降级成 stale
        uc._fetch = _fake_fetch((None, "unreachable"), [])
        uc._cache.attempted_at = time.monotonic() - uc.MIN_FORCE_INTERVAL - 1
        status = await uc.get_update_status(force=True)
    finally:
        uc._fetch = original
        uc.reset()
    assert status["latest"] == "0.4.16"
    assert status["has_update"] is True
    assert status["ok"] is True
    assert status["error"] == "unreachable"
    assert status["stale"] is True


async def test_failure_backs_off_for_ttl():
    calls: list = []
    original = uc._fetch
    uc.reset()
    uc._fetch = _fake_fetch((None, "timeout"), calls)
    try:
        first = await uc.get_update_status()
        second = await uc.get_update_status()      # 退避窗口内：不再出网
    finally:
        uc._fetch = original
        uc.reset()
    assert len(calls) == 1
    assert first["ok"] is False and second["ok"] is False
    assert second["error"] == "timeout"


def test_payload_download_url_only_for_desktop():
    uc.reset()
    try:
        release = uc.parse_release(_github_payload())
        uc._cache.release = release
        uc._cache.fetched_at = time.monotonic()
        with _patched_settings(app_distribution="docker"):
            dock = uc._payload(cached=True)
            assert dock["download_url"] == ""        # 网页版给的是 compose 命令，不是安装包
            assert dock["distribution"] == "docker"
        with _patched_settings(app_distribution="desktop"):
            desk = uc._payload(cached=True)
            assert desk["download_url"].endswith((".exe", ".dmg", ".tar.gz"))
    finally:
        uc.reset()


def test_payload_flags_update_and_current():
    uc.reset()
    try:
        with _patched_settings(app_version="0.4.15"):
            uc._cache.release = uc.parse_release(_github_payload("v0.4.15"))
            uc._cache.fetched_at = time.monotonic()
            same = uc._payload(cached=True)
            assert same["has_update"] is False and same["latest"] == "0.4.15"
            uc._cache.release = uc.parse_release(_github_payload("v0.4.16"))
            newer = uc._payload(cached=True)
            assert newer["has_update"] is True
        uc._cache.release = uc.parse_release(_github_payload("v0.4.14"))
        uc._cache.fetched_at = time.monotonic()
        older = uc._payload(cached=True)
        assert older["has_update"] is False, "本站版本比上游 tag 新时（手动构建）不该提示升级"
    finally:
        uc.reset()


def test_select_release_picks_highest_version():
    """按版本取而不是按发布日期取：热修可能后建、版本却更低。"""
    payload = [_github_payload("v0.4.14"), _github_payload("v0.4.16"), _github_payload("v0.4.15")]
    assert uc.select_release(payload).version == "0.4.16"


def test_stable_channel_ignores_prereleases():
    payload = [_github_payload("v0.5.0-beta.1"), _github_payload("v0.4.16")]
    assert uc.select_release(payload, "stable").version == "0.4.16"
    assert uc.select_release(payload, "beta").version == "0.5.0-beta.1"
    # 镜像只用标记表达预发布（版本号本身看不出后缀）
    flagged = {"version": "v9.9.9", "prerelease": True}
    assert uc.select_release(flagged, "stable") is None
    assert uc.select_release(flagged, "beta").version == "9.9.9"


def test_drafts_are_skipped():
    payload = [{"tag_name": "v0.6.0", "draft": True}, _github_payload("v0.4.16")]
    assert uc.select_release(payload).version == "0.4.16"


def test_select_release_handles_single_object_and_garbage():
    assert uc.select_release(_github_payload("v0.4.16")).version == "0.4.16"
    assert uc.select_release([]) is None
    assert uc.select_release({"message": "Not Found"}) is None
    assert uc.select_release("nope") is None


def test_payload_reports_channel():
    uc.reset()
    try:
        with _patched_settings(update_check_channel="beta"):
            uc._cache.release = uc.parse_release(_github_payload())
            uc._cache.fetched_at = time.monotonic()
            assert uc._payload(cached=True)["channel"] == "beta"
    finally:
        uc.reset()
