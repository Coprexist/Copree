"""头像上传的共用校验。

用户 / AI / 群聊三处头像入口此前各写一遍类型白名单，而且都只信上传方声明的
content-type、不看字节本身：把 SVG（或任意文件）谎报成 image/png 就能过校验，
compress_avatar / make_avatar_thumbnail 解不开时又会把原字节原样退回，最终落成一个
打不开的 .jpg——接口返回 200，界面上却是裂图。校验收到这里，且在覆盖旧头像之前完成。
"""
from __future__ import annotations

import io
import logging

from fastapi import HTTPException, UploadFile, status

logger = logging.getLogger(__name__)

IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
TYPE_ERROR = "仅支持 JPEG/PNG/GIF/WebP 图片"
DECODE_ERROR = "图片无法识别，请换一张"


async def read_avatar(file: UploadFile, max_mb: float) -> bytes:
    """读出头像字节：查类型、查大小、查真伪。任一项不过就抛 400，调用方不必再自己判。"""
    if file.content_type not in IMAGE_TYPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, TYPE_ERROR)
    content = await file.read()
    if len(content) > max_mb * 1024 * 1024:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"头像不能超过 {max_mb:g}MB")
    _ensure_image(content)
    return content


def _ensure_image(content: bytes) -> None:
    """解开一次证明确实是图片；解不开就拒。"""
    try:
        from PIL import Image
    except ImportError:
        # 没装 Pillow 时压缩本来也会跳过，不该反过来拦住上传
        return
    try:
        Image.open(io.BytesIO(content)).verify()
    except Exception as e:
        logger.warning(f"头像字节不是可解码图片: {e}")
        raise HTTPException(status.HTTP_400_BAD_REQUEST, DECODE_ERROR)
