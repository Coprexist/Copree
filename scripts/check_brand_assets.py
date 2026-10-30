"""品牌资产与安装脚本的守卫检查 —— 这两类东西坏过一次，就靠肉眼看不出来。

管三件事：
  1) Windows 快捷方式名不能乱码：.bat 里不许出现非 ASCII 字符（cmd 按控制台代码页读批处理，
     中文字面量落进 .lnk 就成了「å¯¹è¯」）；.bat 必须是 CRLF（LF-only 的批处理在 if 块里会裂）；
     中文必须待在 .ps1 里，且该文件必须是 UTF-8 with BOM（PowerShell 按 BOM 判定编码）。
  2) 桌面图标不能低像素：ICO 必须按档内嵌 16~256，缺档时系统自己缩，糊得更多。
  3) 快捷方式必须显式指向图标（IconLocation），否则换图标后要等图标缓存过期。

用法（仓库根目录，CI 或发版前）：
    python scripts/check_brand_assets.py
"""
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parent.parent
BRAND = ROOT / "frontend" / "public" / "brand"
ICO = BRAND / "coprexist-icon.ico"
PNGS = (32, 180, 192, 288, 512)
BAT = ROOT / "scripts" / "install.bat"
PS1 = ROOT / "scripts" / "create-shortcuts.ps1"

ICO_SIZES = {16, 20, 24, 32, 40, 48, 64, 128, 256}


def check_ico() -> list[str]:
    if not ICO.is_file():
        return [f"缺少 ICO：{ICO.relative_to(ROOT)}"]
    raw = ICO.read_bytes()
    if len(raw) < 6:
        return ["ICO 文件被截断"]
    _reserved, kind, count = struct.unpack("<HHH", raw[:6])
    if kind != 1:
        return ["ICO 文件头不对（type 应为 1）"]
    sizes = set()
    for i in range(count):
        off = 6 + i * 16
        width, height = raw[off], raw[off + 1]
        sizes.add(width or 256)
        sizes.add(height or 256)
    missing = sorted(ICO_SIZES - sizes)
    errors = []
    if missing:
        errors.append(f"ICO 缺这些档位：{missing}（桌面/任务栏缺档会被系统缩放成糊图）")
    return errors


def check_pngs() -> list[str]:
    return [f"缺少 PNG：coprexist-icon-{size}.png"
            for size in PNGS if not (BRAND / f"coprexist-icon-{size}.png").is_file()]


def check_bat() -> list[str]:
    if not BAT.is_file():
        return [f"缺少 {BAT.relative_to(ROOT)}"]
    raw = BAT.read_bytes()
    errors = []
    bad = [(i, line) for i, line in enumerate(raw.split(b"\r\n"), 1)
           if any(byte > 127 for byte in line)]
    if bad:
        lines = "、".join(str(i) for i, _ in bad[:6])
        errors.append(f"{BAT.name} 第 {lines} 行有非 ASCII 字符：cmd 会按代码页解码，"
                      f"中文落进快捷方式就是乱码。请把中文字面量搬进 create-shortcuts.ps1")
    bare_lf = raw.count(b"\n") - raw.count(b"\r\n")
    if bare_lf:
        errors.append(f"{BAT.name} 有 {bare_lf} 处裸 LF：批处理必须是 CRLF，"
                      f"否则 if (...) 块和 goto 会解析错")
    return errors


def check_ps1() -> list[str]:
    if not PS1.is_file():
        return [f"缺少 {PS1.relative_to(ROOT)}"]
    raw = PS1.read_bytes()
    errors = []
    if not raw.startswith(b"\xef\xbb\xbf"):
        errors.append(f"{PS1.name} 必须带 UTF-8 BOM：Windows PowerShell 5.1 没有 BOM 时会按 ANSI 读，"
                      f"中文照样乱码")
    if raw.count(b"\n") - raw.count(b"\r\n"):
        errors.append(f"{PS1.name} 必须用 CRLF 换行")
    text = raw.decode("utf-8-sig", errors="replace")
    if "IconLocation" not in text:
        errors.append(f"{PS1.name} 没设 IconLocation：快捷方式会跟着 exe 图标走，换图标后要等缓存过期")
    return errors


def main() -> int:
    errors: list[str] = []
    for check in (check_ico, check_pngs, check_bat, check_ps1):
        errors.extend(check())
    if errors:
        print("品牌/安装脚本检查未通过：")
        for err in errors:
            print("  ✘", err)
        return 1
    print("品牌/安装脚本检查通过：ICO 九档齐全、PNG 齐全、.bat 纯 ASCII+CRLF、.ps1 带 BOM 且指定图标")
    return 0


if __name__ == "__main__":
    sys.exit(main())
