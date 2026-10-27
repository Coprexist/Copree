"""
Chrome 共享服务 — 单例 Chromium 进程，所有 AI 共用 CDP 端口。

生命周期：FastAPI startup → 检查设置 → 启动 chrome
         管理员开关 → start/stop
         FastAPI shutdown → 清理进程
"""
import asyncio
import logging
import os
import shutil
import signal
import sys
import tempfile
import time
from pathlib import Path

logger = logging.getLogger(__name__)

CDP_PORT = 9222
CDP_ENDPOINT = f"http://127.0.0.1:{CDP_PORT}"
PID_FILE = str(Path(tempfile.gettempdir()) / "chromium_cdp.pid")

# CDP 由 Chromium 内核提供，Chrome、Chromium、Edge 都可用；Windows 上没有装 Chrome
# 时用系统自带的 Edge 兜底，避免插件在 Windows 上直接不可用。
_INSTALL_PATHS = {
    "win32": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ],
    "darwin": [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    ],
}
_DEFAULT_PATHS = [
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
]
_PATH_NAMES = [
    "chromium",
    "chromium-browser",
    "google-chrome",
    "google-chrome-stable",
    "chrome",
    "msedge",
]


def find_chromium() -> str | None:
    """按平台查找可用的 Chromium 系浏览器，找不到返回 None。

    CHROMIUM_BIN / CHROME_BIN 环境变量优先，便于自定义安装路径。
    """
    for env_name in ("CHROMIUM_BIN", "CHROME_BIN"):
        custom = os.environ.get(env_name)
        if custom and os.path.exists(custom):
            return custom
    for path in _INSTALL_PATHS.get(sys.platform, []) + _DEFAULT_PATHS:
        if os.path.exists(path):
            return path
    for name in _PATH_NAMES:
        found = shutil.which(name)
        if found:
            return found
    return None

_chrome_process: asyncio.subprocess.Process | None = None
_start_lock = asyncio.Lock()


def is_running() -> bool:
    """检查 Chromium 是否在运行（通过 CDP 端口探测）"""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1)
        result = s.connect_ex(("127.0.0.1", CDP_PORT))
        s.close()
        return result == 0
    except Exception:
        return False


async def start() -> bool:
    """启动 Chromium headless + CDP。已运行则跳过，带锁防重入。"""
    global _chrome_process

    async with _start_lock:
        if is_running():
            logger.info(f"Chromium CDP 已在运行 (port {CDP_PORT})")
            return True

        # 杀掉旧进程（如果有残留）
        if _chrome_process is not None and _chrome_process.returncode is None:
            try:
                _chrome_process.kill()
                await _chrome_process.wait()
            except Exception:
                pass
            _chrome_process = None

    browser = find_chromium()
    if not browser:
        logger.error(
            "未找到 Chromium 系浏览器，browser 插件不可用；"
            "请安装 Chrome / Chromium，或用环境变量 CHROMIUM_BIN 指定可执行文件路径"
        )
        return False

    profile_dir = Path(tempfile.gettempdir()) / "chromium-cdp-profile"
    profile_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"启动 Chromium headless CDP (port {CDP_PORT}, bin={browser})...")
    if sys.platform.startswith("linux"):
        # 确保 D-Bus 运行（Chrome 渲染引擎依赖）；Windows / macOS 没有这一套
        try:
            await asyncio.create_subprocess_exec("mkdir", "-p", "/run/dbus", stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            # system bus
            sp = await asyncio.create_subprocess_exec("dbus-daemon", "--system", "--fork", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            await sp.wait()
            # session bus
            ss = await asyncio.create_subprocess_exec("dbus-daemon", "--session", "--fork", "--address=unix:path=/tmp/dbus_session", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            await ss.wait()
            os.environ["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=/tmp/dbus_session"
        except Exception: pass
    try:
        _chrome_process = await asyncio.create_subprocess_exec(
            browser,
            "--headless=new",
            "--no-sandbox",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--password-store=basic",
            "--use-mock-keychain",
            "--safebrowsing-disable-auto-update",
            "--disable-extensions",
            "--disable-default-apps",
            "--disable-web-security",
            "--disable-features=SafeBrowsing,SubresourceFilter,ImprovedCookieControls,BlockInsecurePrivateNetworkRequests",
            f"--remote-debugging-port={CDP_PORT}",
            "--remote-debugging-address=127.0.0.1",
            "--remote-allow-origins=*",
            # Chrome 136 起禁止用默认用户数据目录开 CDP 端口，显式指定临时 profile
            f"--user-data-dir={profile_dir}",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            env={**os.environ},
        )

        # 等待端口就绪
        for _ in range(50):  # 最多等 5 秒
            await asyncio.sleep(0.1)
            if is_running():
                logger.info(f"Chromium CDP 已就绪 (pid={_chrome_process.pid})")
                return True
        logger.warning("Chromium 启动超时，但进程可能仍在初始化")
        return is_running()
    except Exception as e:
        logger.error(f"启动 Chromium 失败: {e}")
        return False


async def stop() -> bool:
    """停止 Chromium 进程。"""
    global _chrome_process

    # 先尝试优雅关闭 CDP
    if is_running():
        try:
            import urllib.request
            urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/close", timeout=2)
            await asyncio.sleep(0.5)
        except Exception:
            pass

    if _chrome_process is not None and _chrome_process.returncode is None:
        try:
            _chrome_process.terminate()
            await asyncio.wait_for(_chrome_process.wait(), timeout=5)
        except asyncio.TimeoutError:
            _chrome_process.kill()
        except Exception:
            pass

    # fallback: 杀所有 chromium 进程。仅 POSIX：Windows 上不做全局清理，
    # 否则会误杀用户自己开着的浏览器
    if is_running() and not sys.platform.startswith("win"):
        try:
            proc = await asyncio.create_subprocess_exec(
                "pkill", "-f", "chromium.*remote-debugging-port",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
        except Exception:
            pass

    _chrome_process = None
    logger.info("Chromium CDP 已停止")
    return True
