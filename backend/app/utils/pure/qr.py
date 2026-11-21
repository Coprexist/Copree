"""把一条登录链接画成二维码 PNG。

qrcode 这个库没有进镜像依赖：它只是"把链接变成可扫的图"，而真正需要它的场景
（扫码登录）本来就要用户在场，不值得为此让安装多一个连不上 PyPI 就失败的前置。
库放在 app/_vendor 下，按需加进 sys.path。
"""

from __future__ import annotations

import base64
import io
import sys
from pathlib import Path

_VENDOR = Path(__file__).resolve().parents[2] / "_vendor"
# 画一张码的固定参数：边框留两格静默区、点大小 6px、纠错等级 M —— 手机上扫得动，
# 屏幕上也放得下
_BOX = 6
_BORDER = 16


def png_b64(url: str) -> str:
    """链接 → 二维码 PNG 的 base64；画不出来就返回空串（调用方退回只给链接）"""
    text = (url or "").strip()
    if not text:
        return ""
    if str(_VENDOR) not in sys.path:
        sys.path.insert(0, str(_VENDOR))
    import qrcode  # noqa: PLC0415 —— 只有真要画码时才导入
    from PIL import Image, ImageDraw  # noqa: PLC0415 —— PIL 随镜像提供

    code = qrcode.QRCode(border=2, box_size=1, error_correction=qrcode.constants.ERROR_CORRECT_M)
    code.add_data(text)
    code.make(fit=True)
    matrix = code.get_matrix()
    size = len(matrix) * _BOX + _BORDER * 2
    img = Image.new("RGB", (size, size), "white")
    draw = ImageDraw.Draw(img)
    for y, row in enumerate(matrix):
        for x, dark in enumerate(row):
            if not dark:
                continue
            x0, y0 = _BORDER + x * _BOX, _BORDER + y * _BOX
            draw.rectangle([x0, y0, x0 + _BOX - 1, y0 + _BOX - 1], fill="black")
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode()
