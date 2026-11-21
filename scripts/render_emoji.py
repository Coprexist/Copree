#!/usr/bin/env python3
"""内置表情包（backend/plugins/emoji）的表情图生成脚本。

图来自 **Noto Color Emoji**（Apache License 2.0，可商用），不联网。Pillow 读不了
这套字体的彩色 SVG，只能用它自带的 109px 位图字形（CBDT）渲染；每张渲染后裁掉空白，
居中放进 128×128 的透明画布——表情包的图从此是固定像素，任何系统字体都改不了它的长相。

用法（字体先拷进后端容器）：

    docker cp /usr/share/fonts/truetype/noto/NotoColorEmoji.ttf ai_group_backend:/tmp/
    docker exec ai_group_backend python scripts/render_emoji.py --font /tmp/NotoColorEmoji.ttf

emoji.json 里的 `char` 只为这个脚本服务：平台只认 `file`，渲染层不读 `char`。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SIZE = 109          # NotoColorEmoji 的位图字号，放大或缩小都会报 invalid pixel size
PADDING = 4


def render(ch: str, font: ImageFont.FreeTypeFont, canvas: int) -> Image.Image | None:
    img = Image.new("RGBA", (SIZE * 2, SIZE * 2), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((SIZE // 2, SIZE // 2), ch, font=font, embedded_color=True)
    bbox = img.getbbox()
    if bbox is None:
        return None
    glyph = img.crop(bbox)
    gw, gh = glyph.size
    limit = canvas - PADDING * 2
    scale = min(limit / gw, limit / gh, 1.0)
    if scale < 1.0:
        glyph = glyph.resize((max(1, int(gw * scale)), max(1, int(gh * scale))), Image.LANCZOS)
        gw, gh = glyph.size
    out = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    out.paste(glyph, ((canvas - gw) // 2, (canvas - gh) // 2), glyph)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack", default="backend/plugins/emoji", help="表情包目录")
    ap.add_argument("--font", required=True, help="NotoColorEmoji.ttf")
    ap.add_argument("--canvas", type=int, default=128)
    args = ap.parse_args()

    pack = Path(args.pack)
    manifest = json.loads((pack / "emoji.json").read_text(encoding="utf-8"))
    font = ImageFont.truetype(args.font, SIZE)
    done = 0
    for face in manifest.get("faces", []):
        ch = str(face.get("char") or "").strip()
        if not ch:
            print(f"跳过 {face.get('id')}：没有 char")
            continue
        image = render(ch, font, args.canvas)
        if image is None:
            print(f"跳过 {face.get('id')}：字体里没有 {ch!r}")
            continue
        target = pack / str(face.get("file") or "")
        target.parent.mkdir(parents=True, exist_ok=True)
        image.save(target, optimize=True)
        done += 1
    print(f"已生成 {done} 张表情图 → {pack}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
