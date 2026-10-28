"""扩展名 → MIME 的单一来源。

前端判定「文件卡片 vs 图片」只看 mime 是否 image/*，所以 MIME 记错就是渲染错。
历史上 ai_write_file 一律写 text/plain：AI 自己生成的 SVG/PNG 在聊天里因此退化成
文件卡片（2026-10-28 报障：群里那张 exp_plot.svg 只能点下载）。

为什么不用 mimetypes 当唯一来源：它查 /etc/mime.types，精简镜像里可能缺 .webp/.svg
这类项，同一份数据在宿主与容器里会得到不同结果。显式表在前、mimetypes 兜底，行为可预期。
"""
from __future__ import annotations

import mimetypes
import os

TEXT_PLAIN = "text/plain"

# 「没有信息量」的类型：留成这些值等于没识别，允许用扩展名覆盖
GENERIC_MIMES = frozenset({"", TEXT_PLAIN, "application/octet-stream", "binary/octet-stream"})

# 只列需要纠正的：图片是前端按类型渲染的分支，其余文本类保持 text/plain 更省事
_EXPLICIT = {
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".ico": "image/x-icon",
    ".bmp": "image/bmp",
    ".avif": "image/avif",
}


def guess_mime(path: str | None) -> str:
    """按扩展名猜 MIME，猜不出来按纯文本记（AI 写的东西默认是文本）。"""
    name = str(path or "")
    ext = os.path.splitext(name)[1].lower()
    if ext in _EXPLICIT:
        return _EXPLICIT[ext]
    guessed, _ = mimetypes.guess_type(name)
    return guessed or TEXT_PLAIN


def is_generic(mime: str | None) -> bool:
    """该 MIME 是否等于没识别（空 / text/plain / octet-stream）。"""
    return str(mime or "").strip().lower() in GENERIC_MIMES


def effective_mime(path: str | None, stored: str | None) -> str:
    """登记行里存的值可信就用它，否则按扩展名补一个。

    老数据（存量文件 + 已发出的消息快照）还带着 text/plain，这里兜底，
    免得修复前写下的行一直显示成文件卡片。
    """
    return guess_mime(path) if is_generic(stored) else str(stored or TEXT_PLAIN)
