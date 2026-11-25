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
from app.utils.mime import IMAGE_MIMES

logger = logging.getLogger(__name__)

IMAGE_PLACEHOLDER = "[图片]"
MAX_IMAGE_BYTES = 4 * 1024 * 1024  # 单图上限，与模型侧限制对齐

# 一张图折算成多少「字」：官方视觉 token × 项目的 1.5 字/token（estimate_tokens 的口径）。
# 图片不再按「张数」限制——那是 base64 时代的拍脑袋数；现在按单条消息的字数上限判定，
# 一张图 = 276~1536 字（见 utils/pure/image_tokens.py）。
IMAGE_CHARS_PER_TOKEN = 1.5
# 图片的额外字数余量：0 = 图片与正文**共用**同一个字数上限（用户 2026-11-25 的口径：
# 上限本身就提到 3840 了，不再单独给图片开口子）。
IMAGE_CHAR_HEADROOM = 0

# 单条消息最多注入几张真实图片：None = 不限制（受字数预算约束，见 select_images_for_budget）
DEFAULT_MAX_IMAGES = None

# 尾部 system 便签的识别标记：降级时要靠它把"你能看图"改写成"你看不到图"
IMAGE_NOTE_PREFIX = "【本轮附图】"

VISION_UNSUPPORTED_HINT = (
    "[系统提示] 这条消息带了图片，但当前模型不支持查看图片，你看不到图片内容。"
    "请如实说明你看不到，不要猜测或编造图片内容。"
)


# 模型侧的视觉接口只吃这四种；extension/svg 一律按"看得见但发不过去"处理
# 集合本身在 utils/mime.py（图片口径的单一来源），这里只是沿用旧名字，调用方不用改
VISION_MIMES = IMAGE_MIMES


def is_image(att: dict) -> bool:
    """该附件是否是图片（渲染语义：image/* 都算，含 SVG）。"""
    return str(att.get("mime_type") or "").lower().startswith("image/")


def is_audio(att: dict) -> bool:
    """该附件是否是音频（语音条就是这么渲染的：audio/* 一律按语音处理）。"""
    return str(att.get("mime_type") or "").split(";")[0].strip().lower().startswith("audio/")


# 语音文稿进上下文的上限：它只是"这条说了什么"的摘要，全文靠转写字段本身
TRANSCRIPT_MARK_MAX = 200


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


def image_pixel_size(att: dict, data_dir: str) -> tuple[int, int] | None:
    """图片的像素宽高（只读文件头，不解码整张图）。读不到返回 None。"""
    rel = att.get("path") or ""
    if not rel:
        return None
    physical = os.path.join(data_dir, rel)
    if not os.path.isfile(physical):
        return None
    try:
        from PIL import Image  # 随镜像提供；调用点是 IO，延迟导入不拖慢模块加载

        with Image.open(physical) as img:
            width, height = img.size
        return (int(width), int(height)) if width and height else None
    except Exception as e:
        logger.warning(f"🖼️ 读取图片尺寸失败（按 0 字算）{physical}: {e}")
        return None


def image_display_chars(att: dict, data_dir: str, provider: str = "other") -> int:
    """这张图折算成多少「字」：厂商视觉 token × IMAGE_CHARS_PER_TOKEN。

    只有 DeepSeek 有精确公式；其他厂商走 estimate_image_tokens 的分支（未知 = 1024）。
    读不到尺寸按 0 算——宁可多给一张，也不要因为一个坏文件把整条消息的额度吃掉。
    """
    from app.utils.pure.image_tokens import estimate_image_tokens

    size = image_pixel_size(att, data_dir)
    if not size:
        return 0
    return int(estimate_image_tokens(*size, provider) * IMAGE_CHARS_PER_TOKEN)


def select_images_for_budget(
    images: list[dict], data_dir: str, budget_chars: int | None, provider: str = "other",
) -> list[dict]:
    """按字数预算选图：从**最新**往后累加，超出预算的图降级成占位。

    budget_chars 为 None = 不限制（没有字数上限判定的路径，比如世界对话）。
    至少保留最新的一张：一张都不给等于这次消息的图彻底丢了，而超支一张最多也就 1536 字。
    """
    if budget_chars is None:
        return list(images)
    selected: list[dict] = []
    used = 0
    for att in reversed(images):
        cost = image_display_chars(att, data_dir, provider)
        if selected and used + cost > budget_chars:
            continue
        selected.append(att)
        used += cost
    selected.reverse()
    return selected


def build_content(
    text: str | None,
    attachments,
    data_dir: str,
    *,
    max_images: int | None = DEFAULT_MAX_IMAGES,
    max_bytes: int = MAX_IMAGE_BYTES,
    images: list[dict] | None = None,
) -> str | list[dict]:
    """构建一条消息的 LLM content。

    无可用图片 → 原样返回 text（str）；
    有图       → 返回 parts 列表 [{type: text}, {type: image_url, image_url: {url}}, ...]。

    images：调用方已经按字数预算挑好的图（见 select_images_for_budget）；
    不传则退化成"这条消息里所有可注入的图"。
    """
    if images is None:
        images = image_attachments(attachments)
    if not images:
        return text or ""

    parts: list[dict] = [{"type": "text", "text": text or IMAGE_PLACEHOLDER}]
    injected = 0
    chosen = images if max_images is None else images[:max_images]
    for att in chosen:
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

def image_note(count: int, source: str = "用户最新一条消息") -> str:
    """尾部 system 便签：把"你能看到这张图"作为**平台事实**告诉模型。

    必须是真 system role，不能塞进用户正文——塞正文等于替用户说话，权威性也低。
    实测：只给 image_url 不给这句话，mimo-v2.5 会自称"我是文本 AI"，
    却又能描述出图里的刷新图标。
    source 是图片的来处（用户发的 / 工具返回的）：说清楚才不会让模型把工具截图
    当成用户发来的图去回应用户。
    """
    return f"{IMAGE_NOTE_PREFIX}{source}含 {count} 张图片，你可以直接查看。"


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


def strip_image_bytes(messages: list[dict], max_keep: int = 16) -> list[dict]:
    """落库前的清洗：图片字节换成占位标记，其余内容原样保留。

    对话日志是整份请求体的快照，一条消息里几十 KB 的 base64 会把账本撑爆，
    而回看历史时这些字节也没人读（模型侧下一次本来就只有最新一条带真图）。
    max_keep 是保险：超出这个数的图片直接丢掉，不给脏数据留口子。
    """
    out: list[dict] = []
    left = max_keep
    for m in messages:
        content = m.get("content")
        if not isinstance(content, list):
            out.append(m)
            continue
        parts: list[dict] = []
        changed = False
        for p in content:
            if isinstance(p, dict) and p.get("type") == "image_url":
                changed = True
                if left > 0:
                    left -= 1
                    parts.append({"type": "text", "text": IMAGE_PLACEHOLDER})
                continue
            parts.append(p)
        if not changed:
            out.append(m)
            continue
        new_m = dict(m)
        new_m["content"] = parts
        out.append(new_m)
    return out


# ── 附件在上下文里的两种呈现：标记（每轮都可见）与正文（只给最新一条）──────
# 图片走 image_placeholder/build_content（多模态），其余文件只能靠文字：渲染层铺
# 一行标记，让模型至少知道"对方发了个什么文件"；文本类附件再把内容读进最新一条。
TEXT_MIMES = frozenset({
    "text/plain", "text/markdown", "text/csv", "text/html", "text/xml",
    "application/json", "application/xml", "application/yaml",
})
TEXT_SUFFIXES = (
    ".txt", ".md", ".csv", ".json", ".log", ".yaml", ".yml", ".xml",
    ".html", ".py", ".js", ".ts", ".sql", ".ini", ".toml",
)
INLINE_MAX_BYTES = 64 * 1024     # 再大的文件内联进来只是烧 token
INLINE_MAX_CHARS = 3000          # 一次最多贴这么多字，余下的让 AI 自己问


def is_text_attachment(att: dict) -> bool:
    """这个附件能不能当文本读（按 mime，退一步按扩展名）。"""
    mime = str(att.get("mime_type") or "").split(";")[0].strip().lower()
    if mime in TEXT_MIMES:
        return True
    if mime and mime != "application/octet-stream":
        return False
    name = str(att.get("name") or att.get("path") or "").lower()
    return name.endswith(TEXT_SUFFIXES)


def _size_text(size) -> str:
    try:
        n = int(size or 0)
    except (TypeError, ValueError):
        return ""
    if n <= 0:
        return ""
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / 1024 / 1024:.1f} MB"


def with_transcripts(attachments, transcripts: dict[int, str] | None) -> list | None:
    """把文件级的文稿补进附件快照（纯函数）。

    文稿的家在 `file_metadata.transcript`（唯一来源，见迁移 `0113`），消息快照只留 file_id；
    渲染账本前补一次，AI 才读得到"这条语音说了什么"。已经带了的不覆盖（调用方的引用式补丁优先）。
    """
    items = normalize_attachments(attachments)
    if not items or not transcripts:
        return items
    out: list = []
    for att in items:
        if isinstance(att, dict) and att.get("file_id") and not att.get("transcript"):
            body = transcripts.get(int(att["file_id"]))
            if body:
                att = {**att, "transcript": body}
        out.append(att)
    return out


def emoji_mark(att: dict) -> str:
    """表情附件 → "[表情:名字]"；不是表情返回空串。

    为什么不让标记层自己猜：入口已经把表情落成**普通图片附件**（见
    plugin.catalog.take_image_faces），字节层面它和随手发的一张图完全一样。
    身份只来自 `emoji_asset_file` 写进附件快照的元数据，也只有新消息有——
    已经进账本的旧消息不回溯，仍旧渲染成 [图片]（2026-11-22 定的口径）。
    """
    face = att.get("emoji")
    if not isinstance(face, dict):
        return ""
    name = " ".join(str(face.get("name") or "").split())
    return "[表情:" + name + "]" if name else "[表情]"


def attachment_marks(attachments, *, said: str = "") -> str:
    """把附件铺成一行文字标记：表情 [表情:名字]、图片 [图片]、语音 [语音]（带文稿）、
    其余 [文件] 名（大小）。

    群聊与私信共用（私信原先自己有套 [文件: a, b] 的写法，两处口径会分叉）。
    卡片类附件（好友/群邀请）没有文件语义，不进标记。

    said = 这条消息的正文：正文里已经写了 [图片]，就不再补一个同样的标记——
    通道入站（QQ 图片）的正文本身就带占位，再叠一次会让 AI 和人都看到两个 [图片]。

    语音优先用附件上的 transcript：没有它，AI 只能看到 `voice_20261114033204_49.mp3`
    这种文件名——连自己刚说过什么都不知道（AI 的语音就是它自己的文字念出来的，
    文稿是现成的，见 send_voice）。没有 transcript 的（人的语音、还没转写）退回文件名。
    """
    marks: list[str] = []
    for att in (normalize_attachments(attachments) or []):
        if not isinstance(att, dict) or att.get("type"):
            continue
        if is_image(att):
            mark = emoji_mark(att)
            if mark:
                marks.append(mark)
            elif IMAGE_PLACEHOLDER not in (said or ""):
                marks.append(IMAGE_PLACEHOLDER)
            continue
        if is_audio(att):
            said_text = " ".join(str(att.get("transcript") or "").split())
            if said_text:
                if len(said_text) > TRANSCRIPT_MARK_MAX:
                    said_text = said_text[: TRANSCRIPT_MARK_MAX - 1] + "…"
                marks.append(f"[语音] {said_text}")
            else:
                marks.append("[语音]")
            continue
        name = str(att.get("name") or att.get("path") or "文件")
        size = _size_text(att.get("size"))
        marks.append(f"[文件] {name}（{size}）" if size else f"[文件] {name}")
    return " ".join(marks)


def text_attachment_blocks(
    attachments, data_dir: str, *, max_bytes: int = INLINE_MAX_BYTES, max_chars: int = INLINE_MAX_CHARS
) -> str:
    """最新一条消息里的文本附件：把内容读出来（截断）。

    只给最新一条注入，历史里留标记——与图片注入同一套约定：字节/大段内容只在
    "刚发生的那条"上，不随历史越滚越大。文件本身不可变，读失败只记日志。
    """
    blocks: list[str] = []
    for att in (normalize_attachments(attachments) or []):
        if not isinstance(att, dict) or not is_text_attachment(att):
            continue
        rel = att.get("path") or ""
        if not rel:
            continue
        physical = os.path.join(data_dir, rel)
        if not os.path.isfile(physical):
            logger.warning(f"📄 附件文件不存在，跳过内联: {physical}")
            continue
        try:
            if os.path.getsize(physical) > max_bytes:
                blocks.append(
                    f"[文件] {att.get('name') or rel}（{_size_text(att.get('size'))}，太大没有内联，"
                    "需要内容请让发送者贴出来或分片发）"
                )
                continue
            with open(physical, "r", encoding="utf-8", errors="replace") as f:
                body = f.read()
        except OSError as e:
            logger.warning(f"📄 读取附件失败: {physical}: {e}")
            continue
        clipped = len(body) > max_chars
        head = body[:max_chars]
        note = f"（前 {max_chars} 字，全文 {len(body)} 字）" if clipped else ""
        blocks.append(
            f"[文件内容] {att.get('name') or rel}{note}\n{head}" + ("\n……（后面还有，需要就再问）" if clipped else "")
        )
    return "\n\n".join(blocks)


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
