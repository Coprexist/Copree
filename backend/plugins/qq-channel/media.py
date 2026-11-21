"""QQ 侧的媒体口径 —— 入站取图、出站定类型，两边共用一份

平台（消息表、附件表、多模态上下文）只认 `{file_id, path, name, size, mime_type}`；
QQ 这边要回答的是另外几个问题：事件里的图片地址怎么取（`multimedia.nt.qq.com.cn`
的 download 链接，带鉴权参数、过期即失效）、这个 mime 在官方接口里是 file_type 几号、
多大就不该发。这些是"QQ 的事实"，所以留在插件里，不往平台塞。

与 format.py 的分工：那里是纯字符串（正文、@、引用），这里是字节与协议口径。

为什么不用 Markdown 内嵌图发图（`![](url)`）：那要求机器人有 MD 权限、客户端也得
会渲染 MD——有的 QQ 端根本不显示。图片消息（`msg_type=7` + 先上传拿 `file_info`）
是普通消息类型，与正文格式无关，纯文本模式照样发、对面照样看得见。
"""
from __future__ import annotations

import os

import httpx

from app.utils.channel_media import download as _channel_download
from app.utils.mime import IMAGE_MIMES

from .common import logger

# 官方富媒体的 file_type（QQ 机器人文档「富媒体」）
MEDIA_IMAGE = 1
MEDIA_VIDEO = 2
MEDIA_VOICE = 3
MEDIA_FILE = 4

# 单次上传上限 20MB（官方），base64 后还要 ×4/3：这里按原始字节先卡一道，留出余量
MAX_UPLOAD_BYTES = 15 * 1024 * 1024
# 入站取图的超时与体积上限：QQ 的图通常几百 KB，超过 15MB 的当异常
DOWNLOAD_TIMEOUT = 20.0
DOWNLOAD_MAX_BYTES = 15 * 1024 * 1024

# 只认 QQ 自己的多媒体域：事件里的 url 由腾讯给，但插件不该变成任意 URL 抓取器
ALLOWED_HOSTS = ("multimedia.nt.qq.com.cn", "gchat.qpic.cn", "qq.com")

# 官方图片消息收这几种：IMAGE_MIMES 来自平台（app/utils/mime.py），与多模态同一份口径

_EXT_MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp",
    ".mp3": "audio/mpeg",
}

# 能直接当"语音消息"发（file_type=3）的格式：只有 mp3 是实测过的——
# 2026-11-14 私聊直发，客户端正常播放（不转 silk）。别的音频没验过，别拿
# "接口返回 200"当能用：客户端不认时接口也照样 200，是静默失败。
VOICE_MIMES = frozenset({"audio/mpeg", "audio/mp3"})


def mime_of(att: dict) -> str:
    """附件的 mime：事件给的 content_type 优先，没给就按文件名后缀猜。

    QQ 事件里图片的 content_type 一直是 `image/png` 这类；文件名兜底是为了
    我们自己拼的（站内附件）那份描述也走同一条判断。
    """
    raw = str(att.get("content_type") or att.get("mime_type") or "").split(";")[0].strip().lower()
    if raw:
        return raw
    ext = os.path.splitext(str(att.get("filename") or att.get("name") or ""))[1].lower()
    return _EXT_MIME.get(ext, "")


def is_sendable_image(att: dict) -> bool:
    """这张图能不能发成 QQ 图片消息（官方只收 png/jpeg/gif/webp）"""
    return mime_of(att) in IMAGE_MIMES


def is_sendable_voice(att: dict) -> bool:
    """这段音频能不能直接发成 QQ 语音消息（目前只认 mp3，见 VOICE_MIMES）"""
    return mime_of(att) in VOICE_MIMES


def event_attachments(event: dict) -> list[dict]:
    """QQ 事件里"能取的附件"：有 url 的图片。

    只挑图片：语音要转 silk、视频要转码、普通文件要另走 file_name 那条，都不在这次范围里
    （正文里它们仍是 `[语音]`/`[文件]` 占位，AI 不会瞎猜内容）。
    """
    out: list[dict] = []
    for raw in event.get("attachments") or []:
        if not isinstance(raw, dict):
            continue
        url = str(raw.get("url") or "").strip()
        if not url or not is_sendable_image(raw):
            continue
        out.append({
            "url": url,
            "filename": str(raw.get("filename") or "").strip(),
            "content_type": mime_of(raw),
            "size": int(raw.get("size") or 0),
        })
    return out


def _host_allowed(url: str) -> bool:
    try:
        host = (httpx.URL(url).host or "").lower()
    except Exception:
        return False
    return bool(host) and any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS)


async def download(url: str) -> tuple[bytes, str] | None:
    """取一张 QQ 图片 -> (字节, mime)；取不到返回 None（调用方保留正文里的 [图片]）。

    下载口径（白名单、超时、体积上限）在 app.utils.channel_media 一处：
    qq-napcat 用的是同一份，两条通道不该各写一套"多大算异常"。
    """
    return await _channel_download(url, logger=logger)


def filename_of(att: dict, index: int = 0, mime: str = "") -> str:
    """落库用的文件名：有原名就用原名，否则按 mime 后缀拼一个（去重靠内容哈希，不靠名字）"""
    name = os.path.basename(str(att.get("filename") or "").strip())
    if name:
        return name
    ext = ".jpg"
    for suffix, known in _EXT_MIME.items():
        if known == (mime or mime_of(att)):
            ext = suffix
            break
    return f"qq_{index or 1}{ext}"
