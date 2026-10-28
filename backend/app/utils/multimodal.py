"""附件 → LLM 多模态消息内容。

主站聊天（群聊 / 私信）与群视界世界对话共用的唯一入口：把消息的 attachments
（[{file_id, path, name, size, mime_type}]）转成 OpenAI 兼容的 content。

三条约定（改这里即可，别在调用方各写一遍）：
- 只有光栅图（png/jpeg/gif/webp）进多模态，其余附件降级为文本标记；SVG 虽然也是
  image/*，但供应商的视觉接口不收，且 base64 的 XML 给模型等于噪声；
- 没有可用图片时返回纯字符串——payload 与不带图的消息完全一致，prompt cache 友好；
- 历史消息一律用 image_placeholder() 降级，只让"最新一条"带真实图片字节，避免 token 爆炸。
"""
from __future__ import annotations

import base64
import logging
import os

from app.utils.message_serializer import normalize_attachments

logger = logging.getLogger(__name__)

IMAGE_PLACEHOLDER = "[图片]"
MAX_IMAGE_BYTES = 4 * 1024 * 1024  # 单图上限，与模型侧限制对齐
DEFAULT_MAX_IMAGES = 1  # 单条消息最多注入几张真实图片

# 尾部 system 便签的识别标记：降级时要靠它把"你能看图"改写成"你看不到图"
IMAGE_NOTE_PREFIX = "【本轮附图】"

VISION_UNSUPPORTED_HINT = (
    "[系统提示] 用户发送了图片，但当前模型不支持查看图片，你看不到图片内容。"
    "请如实告诉用户你无法查看图片，不要猜测或编造图片内容。"
)


# 模型侧的视觉接口只吃这四种；extension/svg 一律按"看得见但发不过去"处理
VISION_MIMES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})


def is_image(att: dict) -> bool:
    """该附件是否是图片（渲染语义：image/* 都算，含 SVG）。"""
    return str(att.get("mime_type") or "").lower().startswith("image/")


def is_vision_image(att: dict) -> bool:
    """该附件能不能真的发给视觉模型（光栅图才行）。"""
    mime = str(att.get("mime_type") or "").split(";")[0].strip().lower()
    return mime in VISION_MIMES


def image_attachments(attachments) -> list[dict]:
    """取出可进多模态的图片附件（纯函数，兼容 JSON 字符串 / None / 非法值）。"""
    # normalize_attachments 的契约是 list | None（JSON 坏了 / 传入 None 都返回 None），
    # 所以必须 `or []` 兜底——漏了它会让"没有附件的普通消息"整条路径抛 TypeError
    return [a for a in (normalize_attachments(attachments) or []) if is_vision_image(a)]


def image_placeholder(attachments) -> str:
    """历史消息的图片降级标记：只告诉模型"这里原本有图"，不给字节。

    按 is_image 计数而不是 image_attachments：SVG 发不进视觉接口，但"当时确实有张图"
    是平台事实，历史里不该凭空消失（模型看到 [图片] 才知道别装作看过）。
    """
    n = sum(1 for a in (normalize_attachments(attachments) or []) if is_image(a))
    if n == 0:
        return ""
    return IMAGE_PLACEHOLDER if n == 1 else f"{IMAGE_PLACEHOLDER}×{n}"


def build_content(
    text: str | None,
    attachments,
    data_dir: str,
    *,
    max_images: int = DEFAULT_MAX_IMAGES,
    max_bytes: int = MAX_IMAGE_BYTES,
) -> str | list[dict]:
    """构建一条消息的 LLM content。

    无可用图片 → 原样返回 text（str）；
    有图       → 返回 parts 列表 [{type: text}, {type: image_url, image_url: {url}}, ...]。
    """
    images = image_attachments(attachments)
    if not images:
        return text or ""

    parts: list[dict] = [{"type": "text", "text": text or IMAGE_PLACEHOLDER}]
    injected = 0
    for att in images[:max_images]:
        url = _read_data_url(att, data_dir, max_bytes)
        if url is None:
            continue
        parts.append({"type": "image_url", "image_url": {"url": url}})
        injected += 1

    if injected == 0:
        # 一张都取不到（文件丢失 / 超限）→ 退回纯文本，别把消息变成残缺多模态
        return text or ""
    skipped = len(images) - injected
    if skipped > 0:
        parts.append({"type": "text", "text": f"（本条消息另有 {skipped} 张图片未提供）"})
    return parts


def _read_data_url(att: dict, data_dir: str, max_bytes: int) -> str | None:
    """读取图片并编码成 data URL；失败返回 None（已记日志）。"""
    rel = att.get("path") or ""
    if not rel:
        return None
    physical = os.path.join(data_dir, rel)
    if not os.path.isfile(physical):
        logger.warning(f"🖼️ 图片文件不存在，跳过: {physical}")
        return None
    try:
        size = os.path.getsize(physical)
        if size > max_bytes:
            logger.warning(f"🖼️ 图片过大 ({size} > {max_bytes} bytes)，跳过: {physical}")
            return None
        with open(physical, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")
    except OSError as e:
        logger.warning(f"🖼️ 读取图片失败: {physical}: {e}")
        return None
    logger.info(f"🖼️ 已注入图片: {os.path.basename(rel)} ({size // 1024}KB)")
    mime = att.get("mime_type") or "image/png"
    return f"data:{mime};base64,{b64}"


# ── 视觉不支持时的降级（模型是纯文本，发多模态会被 400 拒掉）────────────

def image_note(count: int) -> str:
    """尾部 system 便签：把"你能看到这张图"作为**平台事实**告诉模型。

    必须是真 system role，不能塞进用户正文——塞正文等于替用户说话，权威性也低。
    实测：只给 image_url 不给这句话，mimo-v2.5 会自称"我是文本 AI"，
    却又能描述出图里的刷新图标。
"""
    return f"{IMAGE_NOTE_PREFIX}用户最新一条消息含 {count} 张图片，你可以直接查看。"


def injected_image_count(content) -> int:
    """多模态 content 里实际注入了几张图（0 = 纯文本）。

    注意取的是**实际注入数**，不是用户发送数：单条默认只注入 1 张，
    剩下的走"另有 N 张未提供"，便签若写总数据模型会去找不存在的图。
    """
    if not isinstance(content, list):
        return 0
    return sum(1 for p in content if isinstance(p, dict) and p.get("type") == "image_url")


def messages_have_images(messages: list[dict]) -> bool:
    """消息列表里是否存在多模态图片部分。"""
    return any(
        isinstance(m.get("content"), list)
        and any(isinstance(p, dict) and p.get("type") == "image_url" for p in m["content"])
        for m in messages
    )


def strip_image_parts(messages: list[dict]) -> tuple[list[dict], int]:
    """剥掉多模态图片部分，换成"看不到图"的文本提示。

    返回 (新消息列表, 剥掉的图片数)；原列表不被修改。
    """
    out: list[dict] = []
    removed = 0
    for m in messages:
        content = m.get("content")
        if isinstance(content, str) and content.startswith(IMAGE_NOTE_PREFIX):
            # 图片被剥掉了，这条"你能看图"的便签必须同步改写成"你看不到"
            new_m = dict(m)
            new_m["content"] = VISION_UNSUPPORTED_HINT
            out.append(new_m)
            continue
        if not isinstance(content, list):
            out.append(m)
            continue
        imgs = [p for p in content if isinstance(p, dict) and p.get("type") == "image_url"]
        if not imgs:
            out.append(m)
            continue
        removed += len(imgs)
        body = "\n".join(
            p.get("text", "") for p in content
            if isinstance(p, dict) and p.get("type") == "text" and p.get("text")
        )
        new_m = dict(m)
        hint = VISION_UNSUPPORTED_HINT
        new_m["content"] = f"{body}\n{hint}".strip() if body else hint
        out.append(new_m)
    return out, removed


def is_vision_unsupported_error(error_text: str) -> bool:
    """从 API 错误文本判断是不是"这个模型不吃图片"。纯函数。

    宁可误判也不能让整轮对话挂掉：误判的代价是降级成"看不到图"（仍可继续对话），
    漏判的代价是用户彻底收不到回复。所以关键词放宽，真实错误文本一并写日志。
    """
    t = (error_text or "").lower()
    if not t:
        return False
    has_image = any(k in t for k in (
        "image", "vision", "modality", "multimodal", "multi-modal", "图片", "图像",
    ))
    has_reject = any(k in t for k in (
        "not support", "unsupported", "does not support", "invalid",
        "cannot", "can not", "unexpected", "unknown", "不支持",
    ))
    return has_image and has_reject
