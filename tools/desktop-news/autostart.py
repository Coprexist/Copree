#!/usr/bin/env python3
"""开机自启安装器：装 / 卸 / 查。

不直接用批处理写启动项，是因为一体机的路径里常带中文和空格，bat 的引号转义极易出错；
用 Python 生成 VBS 启动器（VBS 支持 UTF-16 书写，路径不会乱码），再由 WSH 拉起
pythonw.exe，这条链路在中文 Windows 上最稳。

    python autostart.py install     安装开机自启（写入当前用户的启动文件夹）
    python autostart.py install --delay 25   启动前等待 25 秒，等桌面和网络就绪
    python autostart.py uninstall   卸载
    python autostart.py status      查看当前状态
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
SHORTCUT_NAME = "DesktopNews.vbs"
EXE_NAME = "DesktopNews.exe"


def startup_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise SystemExit("找不到 %APPDATA%，无法定位启动文件夹")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def resolve_launcher() -> tuple[str, str]:
    """返回 (可执行文件, 参数)。打包成 exe 就直接启动 exe，否则用 pythonw + 脚本。"""
    exe = BASE_DIR / EXE_NAME
    if exe.exists():
        return str(exe), ""
    # pythonw 无控制台窗口，开机自启必须用它，否则每次开机弹一个黑框
    pythonw = Path(sys.executable)
    candidate = pythonw.with_name("pythonw.exe")
    if candidate.exists():
        pythonw = candidate
    if "pythonw" not in pythonw.name.lower():
        print(f"警告：未找到 pythonw.exe，将使用 {pythonw.name}（可能会出现控制台窗口）")
    return str(pythonw), f'"{BASE_DIR / "news_desktop.py"}"'


def vbs_content(executable: str, arguments: str, delay_seconds: int) -> str:
    """生成启动器脚本。

    VBS 字符串用两个引号表示一个引号，所以先拼出完整命令行，再整体转义一次——
    分别转义可执行文件和参数会拼出 `& ""path""` 这种非法表达式。
    """
    command = f'"{executable}"' + (f" {arguments}" if arguments else "")
    run = f'"{command.replace(chr(34), chr(34) * 2)}"'
    lines = [
        "' 桌面新闻系统开机自启启动器（由 autostart.py 生成，删除本文件即可取消自启）",
        "Option Explicit",
        "Dim sh, fso",
        'Set sh = CreateObject("WScript.Shell")',
        'Set fso = CreateObject("Scripting.FileSystemObject")',
        f'sh.CurrentDirectory = "{BASE_DIR}"',
        f"WScript.Sleep {max(0, delay_seconds) * 1000}",
        f'If fso.FileExists("{_target_path()}") Then',
        f"  sh.Run {run}, 0, False",
        "End If",
        "",
    ]
    return "\r\n".join(lines)


def _target_path() -> str:
    exe = BASE_DIR / EXE_NAME
    return str(exe if exe.exists() else BASE_DIR / "news_desktop.py")


def install(delay: int) -> int:
    if not sys.platform.startswith("win"):
        print("开机自启只在 Windows 上有意义；其他系统请手动配置。")
        return 2
    target = startup_dir()
    target.mkdir(parents=True, exist_ok=True)
    exe, arguments = resolve_launcher()
    path = target / SHORTCUT_NAME
    # 写 UTF-16 带 BOM：WSH 对 UTF-8 无 BOM 的 VBS 中文路径会乱码
    path.write_text(vbs_content(exe, arguments, delay), encoding="utf-16")
    print(f"已安装开机自启：{path}")
    print(f"  启动命令：{exe} {arguments}")
    print(f"  启动延迟：{delay} 秒")
    print("  取消方式：双击 uninstall_autostart.bat，或删除上面这个文件")
    return 0


def uninstall() -> int:
    path = startup_dir() / SHORTCUT_NAME
    if path.exists():
        path.unlink()
        print(f"已删除开机自启：{path}")
    else:
        print("当前没有安装开机自启。")
    # 一并清掉可能存在的计划任务版本
    try:
        subprocess.run(["schtasks", "/Delete", "/TN", "DesktopNews", "/F"],
                       capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        pass
    return 0


def status() -> int:
    path = startup_dir() / SHORTCUT_NAME
    print(f"启动器路径：{path}")
    print(f"当前状态：{'已安装' if path.exists() else '未安装'}")
    exe, arguments = resolve_launcher()
    print(f"运行方式：{exe} {arguments}")
    data = BASE_DIR / "data"
    latest = data / "latest.json"
    if latest.exists():
        import datetime as dt
        mtime = dt.datetime.fromtimestamp(latest.stat().st_mtime)
        print(f"最近一次抓取：{mtime:%Y-%m-%d %H:%M}")
    else:
        print("最近一次抓取：暂无数据")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="桌面新闻系统开机自启管理")
    parser.add_argument("action", choices=["install", "uninstall", "status"])
    parser.add_argument("--delay", type=int, default=20, help="开机后等待多少秒再启动（默认 20）")
    parser.add_argument("--task", action="store_true", help="改用计划任务方式（可指定延迟，需管理员权限更佳）")
    args = parser.parse_args()

    if args.action == "install":
        if args.task:
            return install_task(args.delay)
        return install(args.delay)
    if args.action == "uninstall":
        return uninstall()
    return status()


def install_task(delay: int) -> int:
    """计划任务方式：比启动文件夹更可靠（不会被「禁用启动项」一键关掉）。"""
    if not sys.platform.startswith("win"):
        print("计划任务只在 Windows 上可用。")
        return 2
    exe, arguments = resolve_launcher()
    command = f'"{exe}" {arguments}'.strip()
    minutes = max(0, delay // 60)
    seconds = max(0, delay % 60)
    cmd = ["schtasks", "/Create", "/SC", "ONLOGON", "/TN", "DesktopNews", "/TR", command, "/F",
           "/DELAY", f"{minutes:04d}:{seconds:02d}"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print("创建计划任务失败：")
        print(result.stdout or result.stderr)
        print("可尝试以管理员身份运行，或改用不带 --task 的方式。")
        return result.returncode
    print(f"已创建计划任务 DesktopNews：{command}（延迟 {delay} 秒）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
