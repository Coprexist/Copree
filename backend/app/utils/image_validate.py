"""图片字节的共用校验 —— 所有「用户传一张图上来」的入口都走这里。

头像上传原先自己写了一份类型白名单与解码校验；表情包如果照抄一遍，就会出现
「同一张谎报成 image/png 的假图，一个入口拒、另一个入口收」这种分叉。
这里只回答一个问题：这串字节真的是图片吗？怎么回错（400 还是别的）留给调用方。

类型白名单不在这里再写一遍：光栅图片的集合定义在 app.utils.mime.IMAGE_MIMES
（视觉接口与官方通道也读它），这里只是它的别名。
"""
from __future__ import annotations

import io
import logging

from app.utils.mime import IMAGE_MIMES

logger = logging.getLogger(__name__)

# 头像 / 表情图 / 视觉接口认的都是同一组光栅图（SVG 拒：同源下带脚本执行面）
IMAGE_TYPES = IMAGE_MIMES


class ImageError(ValueError):
    """上传的字节不是可解码图片"""


def ensure_image(content: bytes, *, what: str = "图片") -> None:
    """解开一次证明确实是图片；解不开抛 ImageError（ValueError 子类）。

    Pillow 没装时直接放行：压缩/缩略图本来也会跳过，不该反过来拦住上传。
    """
    try:
        from PIL import Image
    except ImportError:
        return
    try:
        Image.open(io.BytesIO(content)).verify()
    except Exception as e:
        logger.warning(f"{what}字节不是可解码图片: {e}")
        raise ImageError(f"{what}无法识别，请换一张") from e
