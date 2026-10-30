"""从品牌主图重新生成站点与桌面端需要的图标文件。

主图只有一张：frontend/public/brand/coprexist-icon-512.png。ICO（Windows）、
ICNS（macOS）与各档 PNG 都由它派生，改图只需替换主图再跑一次：

    python scripts/make_app_icons.py            # 在仓库根目录跑
    python scripts/make_app_icons.py <root>     # 指定仓库根（打包流水线用）

为什么小尺寸要单独处理：资源管理器、任务栏、Alt-Tab 实际取的是 16/24/32/48 这几档，
而主图上的双环描边只有 2.5/32。直接把 512 用 LANCZOS 缩到 16px，环会糊成一圈灰边——
用户看到的就是"图标低清"。所以小尺寸缩放后补一次锐化，并且**逐档**写进 ICO，
不让 Pillow 从 256 再缩一遍（那等于把锐化成果抹掉）。

Pillow 已在 backend/requirements.txt 里，构建流水线直接复用，不用额外依赖。
"""
from pathlib import Path
import struct
import sys

from PIL import Image, ImageFilter

ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
BRAND = ROOT / "frontend" / "public" / "brand"
MASTER = BRAND / "coprexist-icon-512.png"

PNG_SIZES = (32, 180, 192, 288)

# Windows 会按显示场景自己挑档：16（列表）、20/24（Explorer 中图标）、32（桌面/任务栏）、
# 48（大图标）、64/128/256（属性页、超高 DPI）。缺哪档它就自己缩放，糊得更多。
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)

# 小尺寸补锐化的强度：环的描边在这个尺寸下只有 1~2px，不补会糊；补过头会出青色描边光晕
# （16px 上用 percent=140 实测就是这样）。所以强度按尺寸分档，越大越敢补。
_SHARPEN_STEPS = (
    (20, 0.5, 70, 1),
    (32, 0.6, 80, 1),
    (48, 0.8, 100, 2),
)

# macOS 图标包里每种尺寸都有自己的类型码，全部内嵌 PNG。
ICNS_ENTRIES = (
    (b"icp4", 16),
    (b"icp5", 32),
    (b"ic07", 128),
    (b"ic08", 256),
    (b"ic09", 512),
    (b"ic10", 1024),
)


def _load_master() -> Image.Image:
    if not MASTER.is_file():
        raise SystemExit(f"缺少品牌主图：{MASTER}")
    return Image.open(MASTER).convert("RGBA")


def _sharpen(size: int) -> tuple[float, int, int] | None:
    """这一档该用多强的锐化：(radius, percent, threshold)；大尺寸不锐化，返回 None。"""
    for limit, radius, percent, threshold in _SHARPEN_STEPS:
        if size <= limit:
            return radius, percent, threshold
    return None


def _frame(master: Image.Image, size: int) -> Image.Image:
    """一档尺寸的成品图：缩到该尺寸，小档按档补锐化。所有输出都走它，口径只有一处。"""
    out = master.resize((size, size), Image.LANCZOS)
    params = _sharpen(size)
    if params is not None:
        out = out.filter(ImageFilter.UnsharpMask(radius=params[0], percent=params[1],
                                                 threshold=params[2]))
    return out


def write_pngs(master: Image.Image) -> list[Path]:
    written = []
    for size in PNG_SIZES:
        path = BRAND / f"coprexist-icon-{size}.png"
        _frame(master, size).save(path, format="PNG", optimize=True)
        written.append(path)
    return written


def write_ico(master: Image.Image) -> Path:
    """逐档写 ICO（每帧都是自己生成的 PNG）。

    不用 Image.save(format="ICO", sizes=...)：那样 Pillow 只留一份主图，读的时候按尺寸现缩，
    我们为小档做的锐化会全部丢掉——这正是一个图标"看着糊"却查不出原因的地方。
    """
    import io

    frames = []
    for size in ICO_SIZES:
        buf = io.BytesIO()
        _frame(master, size).save(buf, format="PNG", optimize=True)
        frames.append((size, buf.getvalue()))

    header = struct.pack("<HHH", 0, 1, len(frames))
    offset = 6 + 16 * len(frames)
    entries, blobs = b"", b""
    for size, payload in frames:
        dim = 0 if size >= 256 else size        # 256 在目录里记作 0
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(payload), offset)
        offset += len(payload)
        blobs += payload
    path = BRAND / "coprexist-icon.ico"
    path.write_bytes(header + entries + blobs)
    return path


def write_icns(master: Image.Image) -> Path:
    import io

    chunks = []
    for type_code, size in ICNS_ENTRIES:
        buf = io.BytesIO()
        _frame(master, size).save(buf, format="PNG")
        payload = buf.getvalue()
        chunks.append(type_code + struct.pack(">I", len(payload) + 8) + payload)
    body = b"".join(chunks)
    data = b"icns" + struct.pack(">I", len(body) + 8) + body
    path = BRAND / "coprexist-icon.icns"
    path.write_bytes(data)
    return path


def main() -> None:
    master = _load_master()
    paths = write_pngs(master) + [write_ico(master), write_icns(master)]
    for path in paths:
        print(f"{path.relative_to(ROOT)} {path.stat().st_size} B")


if __name__ == "__main__":
    main()
