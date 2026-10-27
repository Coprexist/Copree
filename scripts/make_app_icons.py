"""从品牌主图重新生成站点与桌面端需要的图标文件。

主图只有一张：frontend/public/brand/coprexist-icon-512.png。ICO（Windows）、
ICNS（macOS）与各档 PNG 都由它派生，改图只需替换主图再跑一次：

    python scripts/make_app_icons.py

Pillow 已在 backend/requirements.txt 里，构建流水线直接复用，不用额外依赖。
"""
from pathlib import Path
import struct

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
BRAND = ROOT / "frontend" / "public" / "brand"
MASTER = BRAND / "coprexist-icon-512.png"

PNG_SIZES = (32, 180, 192, 288)
ICO_SIZES = ((16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256))
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


def _resized(img: Image.Image, size: int) -> Image.Image:
    return img.resize((size, size), Image.LANCZOS)


def write_pngs(img: Image.Image) -> list[Path]:
    written = []
    for size in PNG_SIZES:
        path = BRAND / f"coprexist-icon-{size}.png"
        _resized(img, size).save(path, format="PNG", optimize=True)
        written.append(path)
    return written


def write_ico(img: Image.Image) -> Path:
    path = BRAND / "coprexist-icon.ico"
    img.save(path, format="ICO", sizes=list(ICO_SIZES))
    return path


def write_icns(img: Image.Image) -> Path:
    import io

    chunks = []
    for type_code, size in ICNS_ENTRIES:
        buf = io.BytesIO()
        _resized(img, size).save(buf, format="PNG")
        payload = buf.getvalue()
        chunks.append(type_code + struct.pack(">I", len(payload) + 8) + payload)
    body = b"".join(chunks)
    data = b"icns" + struct.pack(">I", len(body) + 8) + body
    path = BRAND / "coprexist-icon.icns"
    path.write_bytes(data)
    return path


def main() -> None:
    img = _load_master()
    paths = write_pngs(img) + [write_ico(img), write_icns(img)]
    for path in paths:
        print(f"{path.relative_to(ROOT)} {path.stat().st_size} B")


if __name__ == "__main__":
    main()
