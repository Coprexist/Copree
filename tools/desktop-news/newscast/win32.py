"""Win32 桌面层：把窗口压到最底层、抠色透明、鼠标穿透、全局热键。

用 ctypes 直接调 user32，不依赖 pywin32——目标机只装官方 Python 就能跑。
非 Windows 平台全部退化为空实现，界面仍能以普通窗口打开，方便在别的系统上调试排版。

三种置底方式（config.ui.desktop_mode）：
- workerw：窗口挂到壁纸层 WorkerW 下，成为桌面的一部分，连「显示桌面」都不会把它藏起来，
          代价是会被桌面图标盖住；
- bottom ：用 SetWindowPos(HWND_BOTTOM) 反复压到最底层，在图标之上、所有窗口之下；
- normal ：普通窗口，仅调试用。
"""

from __future__ import annotations

import ctypes
import logging
import sys

IS_WINDOWS = sys.platform.startswith("win")

logger = logging.getLogger("newscast")

# ---- Win32 常量 ----
HWND_BOTTOM = 1
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
SWP_NOOWNERZORDER = 0x0200
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
LWA_COLORKEY = 0x00000001
LWA_ALPHA = 0x00000002
SMTO_NORMAL = 0x0000
SW_SHOWNOACTIVATE = 4

# 虚拟键码
_VK = {
    "ctrl": 0x11, "control": 0x11, "alt": 0x12, "menu": 0x12, "shift": 0x10, "win": 0x5B,
    "space": 0x20, "esc": 0x1B, "enter": 0x0D, "tab": 0x09,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
}
for _i in range(1, 25):
    _VK[f"f{_i}"] = 0x6F + _i
for _c in "abcdefghijklmnopqrstuvwxyz":
    _VK[_c] = ord(_c.upper())
for _d in "0123456789":
    _VK[_d] = ord(_d)

if IS_WINDOWS:
    _user32 = ctypes.windll.user32
    try:
        _dwmapi = ctypes.windll.dwmapi
    except OSError:
        _dwmapi = None

    # 64 位下窗口句柄、样式、LRESULT 都是 8 字节。ctypes 默认按 c_int 处理返回值，
    # 句柄会被截断成 32 位，之后传给其他 API 全部失效——所以每个返回句柄的函数都必须声明。
    _LONG_PTR = ctypes.c_ssize_t
    _HANDLE = ctypes.c_void_p

    def _decl(name, argtypes, restype):
        fn = getattr(_user32, name, None)
        if fn is not None:
            fn.argtypes = argtypes
            fn.restype = restype
        return fn

    _GetWindowLongPtr = getattr(_user32, "GetWindowLongPtrW", None) or _user32.GetWindowLongW
    _SetWindowLongPtr = getattr(_user32, "SetWindowLongPtrW", None) or _user32.SetWindowLongW
    _GetWindowLongPtr.restype = _LONG_PTR
    _GetWindowLongPtr.argtypes = [_HANDLE, ctypes.c_int]
    _SetWindowLongPtr.restype = _LONG_PTR
    _SetWindowLongPtr.argtypes = [_HANDLE, ctypes.c_int, _LONG_PTR]

    _decl("GetParent", [_HANDLE], _HANDLE)
    _decl("FindWindowW", [ctypes.c_wchar_p, ctypes.c_wchar_p], _HANDLE)
    _decl("FindWindowExW", [_HANDLE, _HANDLE, ctypes.c_wchar_p, ctypes.c_wchar_p], _HANDLE)
    _decl("SetParent", [_HANDLE, _HANDLE], _HANDLE)
    _decl("SetWindowPos", [_HANDLE, _HANDLE, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                           ctypes.c_int, ctypes.c_uint], ctypes.c_int)
    _decl("ShowWindow", [_HANDLE, ctypes.c_int], ctypes.c_int)
    _decl("SetLayeredWindowAttributes", [_HANDLE, ctypes.c_uint32, ctypes.c_ubyte,
                                         ctypes.c_uint32], ctypes.c_int)
    _decl("GetMonitorInfoW", [_HANDLE, ctypes.c_void_p], ctypes.c_int)
    _decl("SendMessageTimeoutW", [_HANDLE, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t,
                                  ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p], _LONG_PTR)
    _decl("GetAsyncKeyState", [ctypes.c_int], ctypes.c_short)


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", RECT),
                ("rcWork", RECT), ("dwFlags", ctypes.c_ulong)]


def hwnd_of(widget) -> int:
    """取 tkinter 窗口的真实句柄。winfo_id() 给的是子窗口，要向上找到顶层窗口。"""
    if not IS_WINDOWS:
        return 0
    try:
        widget.update_idletasks()
        hwnd = int(widget.winfo_id())
        parent = _user32.GetParent(ctypes.c_void_p(hwnd))
        # Tk 的 toplevel 常常嵌在 wrapper window 里，取最外层祖先
        while parent:
            hwnd = parent
            parent = _user32.GetParent(ctypes.c_void_p(hwnd))
        return hwnd
    except Exception:
        return 0


def _ex_style(hwnd: int) -> int:
    return int(_GetWindowLongPtr(ctypes.c_void_p(hwnd), GWL_EXSTYLE))


def _set_ex_style(hwnd: int, style: int) -> None:
    _SetWindowLongPtr(ctypes.c_void_p(hwnd), GWL_EXSTYLE, _LONG_PTR(style))


def apply_layered(hwnd: int, alpha: float, color_key: str | None) -> bool:
    """同时设置整窗透明与抠色透明。

    tkinter 的 -alpha 与 -transparentcolor 各自调用一次 SetLayeredWindowAttributes，
    后一次会覆盖前一次的标志位，导致「只有一种透明生效」。这里合并两个标志位重设一次。
    """
    if not IS_WINDOWS or not hwnd:
        return False
    try:
        style = _ex_style(hwnd) | WS_EX_LAYERED
        _set_ex_style(hwnd, style)
        key = _colorref(color_key) if color_key else 0
        flags = LWA_ALPHA | (LWA_COLORKEY if color_key else 0)
        ok = _user32.SetLayeredWindowAttributes(ctypes.c_void_p(hwnd), ctypes.c_uint32(key),
                                                ctypes.c_ubyte(max(0, min(255, int(alpha * 255)))),
                                                ctypes.c_uint(flags))
        return bool(ok)
    except Exception as exc:
        logger.warning("设置分层窗口失败：%s", exc)
        return False


def _colorref(color: str) -> int:
    """'#RRGGBB' -> Win32 COLORREF（0x00BBGGRR，注意是 BGR 顺序）。"""
    text = (color or "").lstrip("#")
    if len(text) != 6:
        return 0
    r, g, b = int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)
    return (b << 16) | (g << 8) | r


def set_click_through(hwnd: int, enable: bool, color_key: str | None = None) -> None:
    """整窗鼠标穿透：开启后鼠标事件完全落到下层窗口，屏幕变成纯展示。

    注意与抠色透明不同——抠色透明只让「透明色区域」穿透，设了 WS_EX_TRANSPARENT
    则是整块面板都穿透，用户再也点不到界面上任何东西（热键仍可用）。
    """
    if not IS_WINDOWS or not hwnd:
        return
    try:
        style = _ex_style(hwnd)
        style |= WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW  # 不进任务栏、不抢焦点
        if enable:
            style |= WS_EX_TRANSPARENT | WS_EX_LAYERED
        else:
            style &= ~WS_EX_TRANSPARENT
        _set_ex_style(hwnd, style)
    except Exception as exc:
        logger.warning("设置鼠标穿透失败：%s", exc)


def push_to_bottom(hwnd: int) -> bool:
    """把窗口压到最底层（图标层之上、所有窗口之下）。"""
    if not IS_WINDOWS or not hwnd:
        return False
    try:
        return bool(_user32.SetWindowPos(ctypes.c_void_p(hwnd), ctypes.c_void_p(HWND_BOTTOM),
                                         0, 0, 0, 0,
                                         SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOOWNERZORDER))
    except Exception:
        return False


def attach_to_desktop(hwnd: int) -> bool:
    """把窗口挂到壁纸层 WorkerW 下（Rainmeter / Wallpaper Engine 的做法）。

    步骤：给 Progman 发 0x052C 让它生成 WorkerW；再枚举找到包含 SHELLDLL_DefView 的窗口，
    它后面那个 WorkerW 才是放壁纸的层，把我们的窗口 SetParent 过去。
    """
    if not IS_WINDOWS or not hwnd:
        return False

    def _find_defview() -> int:
        """找到定义桌面图标的 WorkerW/Progman 窗口。"""
        found = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def _enum(top, _param):
            shell = _user32.FindWindowExW(ctypes.c_void_p(top), None, "SHELLDLL_DefView", None)
            if shell:
                found.append(int(top))
                return False
            return True

        _user32.EnumWindows(_enum, None)
        return found[0] if found else 0

    try:
        progman = _user32.FindWindowW("Progman", None)
        result = ctypes.c_ulong()
        if progman:
            # 0x052C 会让 Progman 派生 WorkerW；不发这一步，很多系统上根本找不到目标层
            _user32.SendMessageTimeoutW(ctypes.c_void_p(progman), 0x052C, 0, 0, SMTO_NORMAL, 1000,
                                        ctypes.byref(result))
        defview_host = _find_defview()
        target = 0
        if defview_host:
            target = int(_user32.FindWindowExW(None, ctypes.c_void_p(defview_host), "WorkerW", None) or 0)
        if not target:
            target = int(progman or 0)
        if not target:
            return False
        ok = bool(_user32.SetParent(ctypes.c_void_p(hwnd), ctypes.c_void_p(target)))
        if ok:
            # 成为子窗口后要显式显示，否则 SetParent 会把它藏起来
            _user32.ShowWindow(ctypes.c_void_p(hwnd), SW_SHOWNOACTIVATE)
            logger.info("已挂到桌面壁纸层（WorkerW）")
        return ok
    except Exception as exc:
        logger.warning("挂载壁纸层失败，退回置底模式：%s", exc)
        return False


def place_window(hwnd: int, x: int, y: int, width: int, height: int) -> None:
    """按屏幕绝对坐标摆放窗口（挂到壁纸层后必须用绝对坐标）。"""
    if not IS_WINDOWS or not hwnd:
        return
    try:
        _user32.SetWindowPos(ctypes.c_void_p(hwnd), None, int(x), int(y), int(width), int(height),
                             SWP_NOACTIVATE | SWP_NOOWNERZORDER | SWP_SHOWWINDOW)
    except Exception as exc:
        logger.warning("摆放窗口失败：%s", exc)


def monitors() -> list[dict]:
    """枚举所有显示器，返回 [{x,y,width,height,primary}]；非 Windows 返回空列表。"""
    if not IS_WINDOWS:
        return []
    out: list[dict] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(RECT),
                        ctypes.c_double)
    def _enum(hmon, _hdc, _rect, _data):
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if _user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            out.append({
                "x": info.rcMonitor.left, "y": info.rcMonitor.top,
                "width": info.rcMonitor.right - info.rcMonitor.left,
                "height": info.rcMonitor.bottom - info.rcMonitor.top,
                "primary": bool(info.dwFlags & 1),
            })
        return True

    try:
        _user32.EnumDisplayMonitors(None, None, _enum, None)
    except Exception as exc:
        logger.warning("枚举显示器失败：%s", exc)
    out.sort(key=lambda m: (not m["primary"], m["x"], m["y"]))
    return out


def set_dpi_awareness() -> None:
    """声明 DPI 感知：不声明的话在 125%/150% 缩放的机器上界面会被系统拉伸变糊。"""
    if not IS_WINDOWS:
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


# ---------------------------------------------------------------- 全局热键

class HotkeyWatcher:
    """轮询 GetAsyncKeyState 实现的全局热键。

    比 RegisterHotKey + 子类化 WndProc 更稳：不接管 Tk 的窗口过程，
    不会因为消息转发出错把界面搞死，代价是 200ms 的检测延迟。
    """

    def __init__(self, bindings: dict[str, str]) -> None:
        self.bindings: list[tuple[str, frozenset[str], int]] = []
        for action, combo in (bindings or {}).items():
            parsed = self._parse(combo)
            if parsed:
                self.bindings.append((action, parsed[0], parsed[1]))
        self._down: set[str] = set()

    @staticmethod
    def _parse(combo: str) -> tuple[frozenset[str], int] | None:
        if not combo:
            return None
        parts = [p.strip().lower() for p in str(combo).replace("+", " ").split() if p.strip()]
        mods, key = set(), None
        for part in parts:
            if part in ("ctrl", "control"):
                mods.add("ctrl")
            elif part in ("alt", "menu"):
                mods.add("alt")
            elif part == "shift":
                mods.add("shift")
            elif part in ("win", "super"):
                mods.add("win")
            else:
                key = _VK.get(part)
        if key is None:
            return None
        return frozenset(mods), key

    @staticmethod
    def _pressed(vk: int) -> bool:
        if not IS_WINDOWS:
            return False
        return bool(_user32.GetAsyncKeyState(vk) & 0x8000)

    def poll(self) -> list[str]:
        """返回本次新触发的动作（按住不松只触发一次）。"""
        if not IS_WINDOWS or not self.bindings:
            return []
        fired: list[str] = []
        mods_state = {
            "ctrl": self._pressed(_VK["ctrl"]),
            "alt": self._pressed(_VK["alt"]),
            "shift": self._pressed(_VK["shift"]),
            "win": self._pressed(_VK["win"]),
        }
        for action, mods, vk in self.bindings:
            active = self._pressed(vk) and all(mods_state.get(m, False) for m in mods)
            # 要求声明的修饰键全部按下；未声明的修饰键不限制（避免和输入法冲突时完全失灵）
            if active and action not in self._down:
                self._down.add(action)
                fired.append(action)
            elif not active and action in self._down:
                self._down.discard(action)
        return fired
