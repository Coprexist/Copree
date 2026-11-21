"""用户自制表情包 —— 创建者自己的表情，落成 emojipack 插件。

为什么不新发明一种存储：表情包在平台里的唯一形态就是 emojipack 插件
（目录 + plugin.json + emoji.json + 图）。装/卸/全局开关/个人开关/商城/渲染/入口归一
全都认这一个形状。用户自制只是"这个包是谁的"多了一个作者字段，没有第二种格式——
写进同一个 plugins/ 目录，归属记在 manifest 的 owner_user_id 上，写完就是一个可用的包。

归属与权限：
- owner_user_id 是"谁能改这个包"的唯一依据：只有创建者能加/删表情、删包；
- 商城装的、内置的包没有这一项 → 这里一律改不了，要卸仍走插件管理（管理员边界）。

改完盘上的 JSON 之后必须 invalidate_caches()：catalog 按目录签名缓存 30 秒，
不清的话新表情要等下一次过期才出现在入口归一与 AI 的选择器里。
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from app.services.plugin import catalog, store
from app.utils.image_validate import IMAGE_TYPES, ImageError, ensure_image

logger = logging.getLogger(__name__)

# 用户包 id 前缀 + 归属：emoji-u12-xiao-mao 一眼看出是谁做的，卸载/清理也按它认
PACK_ID_PREFIX = "emoji-"
OWNER_KEY = "owner_user_id"

MAX_PACKS_PER_USER = 20          # 一个人最多几个包：面板一屏能看完，也不至于堆满 data/plugins
FACE_IMAGE_MAX_MB = 4            # 表情图上限：聊天里发得动，也顺带挡住把大图当表情传
MAX_PACK_ID = 40                 # 与 store.slug_ok 的上限一致（目录名）
PACK_NAME_MAX = 40
FACE_NAME_MAX = 24
CAPTION_MAX = 160
KEYWORDS_MAX = 120
MOOD_MAX = 24
SLUG_MAX = 20                    # id 里留给包名的那一段

# 默认的用法说明：用户不写也能用，写了就覆盖。这段是给 AI 看的，不是界面文案
USAGE_DEFAULT = (
    "用户自制的表情包。发之前先看每个表情的说明（caption / keywords / mood），"
    "对得上当前场合再发；说明里没写清楚的，宁可不发。"
)

# 后缀按声明的 content-type 定：文件名不沿用上传方给的名字，避免奇怪字符与撞名
IMAGE_SUFFIX = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
}


class EmojiPackError(ValueError):
    """表情包操作不合法（权限或内容问题，文本直接给人看）"""


# ── 目录与归属 ──────────────────────────────────────────────

def owner_of(manifest: dict[str, Any]) -> int | None:
    """manifest 里声明的作者用户 id（没有/不合法 = 平台包，不能在这里改）"""
    raw = manifest.get(OWNER_KEY)
    try:
        return int(raw) if raw not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _pack_dir(manifest: dict[str, Any]) -> Path:
    """插件目录（scan 得到的 _dir），并确认它确实在用户插件根下"""
    path = Path(manifest.get("_dir") or "")
    root = catalog.USER_PLUGIN_DIR.resolve()
    try:
        resolved = path.resolve()
    except OSError as e:
        raise EmojiPackError("表情包目录不可用") from e
    if root not in resolved.parents:
        raise EmojiPackError("拒绝访问插件目录之外的路径")
    return resolved


def _owned(pack_id: str, user_id: int) -> dict[str, Any]:
    """取一个属于这个用户的表情包；不存在/不是表情包/不是他的，一律同一句话"""
    manifest = catalog.scan_disk().get(str(pack_id or "")) or {}
    if manifest.get("category") != "emojipack" or owner_of(manifest) != int(user_id):
        raise EmojiPackError("表情包不存在或不属于你")
    return manifest


def _write_json(path: Path, data: dict[str, Any]) -> None:
    """先写临时文件再替换：别的请求可能正好在扫目录，读不到半截 JSON"""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _payload(manifest: dict[str, Any]) -> dict[str, Any]:
    return catalog.load_entry_payload(manifest)


# ── 读 ─────────────────────────────────────────────────────

async def list_mine(db, user_id: int) -> list[dict[str, Any]]:
    """我做的表情包（含表情与全局开关状态）

    全局开关只读不写：那是管理员的事；这里显示出来，创建者才知道
    "为什么我建了包，AI 那边却看不到"。
    """
    from sqlalchemy import select

    from app.models.plugin import Plugin

    mine: list[dict[str, Any]] = []
    for pack_id, manifest in sorted(catalog.scan_disk().items()):
        if manifest.get("category") != "emojipack" or owner_of(manifest) != int(user_id):
            continue
        pack = catalog.get_emoji_pack(manifest) or {"usage": "", "faces": []}
        mine.append({
            "id": pack_id,
            "name": str(manifest.get("name") or pack_id),
            "usage": pack["usage"],
            "faces": pack["faces"],
            "enabled": True,
        })
    if mine:
        rows = (await db.execute(
            select(Plugin.id, Plugin.enabled).where(Plugin.id.in_([p["id"] for p in mine]))
        )).all()
        enabled = {str(pid): bool(flag) for pid, flag in rows}
        for pack in mine:
            pack["enabled"] = enabled.get(pack["id"], True)
    return mine


# ── 写 ─────────────────────────────────────────────────────

def _slug(text: str) -> str:
    """包名 → 目录名里那一段 ASCII（中文名留不出东西时退回 pack）"""
    slug = re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")
    return slug[:SLUG_MAX].strip("-")


def _new_pack_id(user_id: int, name: str) -> str:
    """给我一个新的包 id：emoji-u<id>-<slug>，重名就加 -2、-3（目录即 id，不能覆盖别人的）"""
    base = f"{PACK_ID_PREFIX}u{int(user_id)}-"
    slug = _slug(name) or "pack"
    candidate = (base + slug)[:MAX_PACK_ID].rstrip("-")
    n = 2
    while (catalog.USER_PLUGIN_DIR / candidate).exists():
        suffix = f"-{n}"
        candidate = (base + slug)[:MAX_PACK_ID - len(suffix)].rstrip("-") + suffix
        n += 1
    return candidate


async def create_pack(
    db, *, user_id: int, name: str, usage: str = "", author: str = "",
) -> dict[str, Any]:
    """新建一个空表情包（目录 + plugin.json + emoji.json），并立刻落成插件行"""
    name = str(name or "").strip()[:PACK_NAME_MAX]
    if not name:
        raise EmojiPackError("给表情包起个名字")
    if len(await list_mine(db, user_id)) >= MAX_PACKS_PER_USER:
        raise EmojiPackError(f"最多建 {MAX_PACKS_PER_USER} 个表情包")

    pack_id = _new_pack_id(user_id, name)
    if not store.slug_ok(pack_id):
        raise EmojiPackError("表情包名无法生成合法的 id，换一个")

    root = catalog.USER_PLUGIN_DIR
    root.mkdir(parents=True, exist_ok=True)
    target = root / pack_id
    target.mkdir()
    manifest = {
        "id": pack_id,
        "name": name,
        "description": f"{author or '用户'}自制的表情包".strip()[:2000],
        "category": "emojipack",
        "version": "1.0.0",
        "author": str(author or "")[:80],
        "icon": "",
        "entry": catalog.ENTRY_FILE["emojipack"],
        # 建好就能在插件里看到并勾给 AI 用；皮肤那种默认关特例不适用于表情
        "default_enabled": True,
        OWNER_KEY: int(user_id),
    }
    try:
        _write_json(target / catalog.MANIFEST_NAME, manifest)
        _write_json(target / catalog.ENTRY_FILE["emojipack"], {
            "usage": str(usage or "").strip()[:200] or USAGE_DEFAULT,
            "faces": [],
        })
    except OSError as e:
        raise EmojiPackError(f"写表情包失败：{e}") from e
    logger.info("用户 %s 新建表情包 %s", user_id, pack_id)

    catalog.invalidate_caches()
    await catalog.sync_plugins_to_db(db)
    return {"id": pack_id, "name": name, "faces": []}


async def add_face(
    db,
    *,
    user_id: int,
    pack_id: str,
    name: str,
    caption: str,
    content: bytes,
    content_type: str,
    keywords: str = "",
    mood: str = "",
    emoji: str = "",
) -> dict[str, Any]:
    """给包加一个表情：校验图 → 落图 → 写 emoji.json

    caption 必须写：一个只有名字的表情，AI 拿不出"这张图什么时候该发"的判断，
    等于没装。这也是这套东西存在的理由。
    """
    manifest = _owned(pack_id, user_id)
    directory = _pack_dir(manifest)
    payload = _payload(manifest)
    faces = [f for f in (payload.get("faces") or []) if isinstance(f, dict)]
    if len(faces) >= catalog.EMOJI_FACE_MAX:
        raise EmojiPackError(f"一个表情包最多 {catalog.EMOJI_FACE_MAX} 个表情")

    name = str(name or "").strip()[:FACE_NAME_MAX]
    if not name:
        raise EmojiPackError("给这个表情起个名字")
    caption = str(caption or "").strip()[:CAPTION_MAX]
    if not caption:
        raise EmojiPackError("写一句给 AI 看的说明（这张图是什么、什么时候发）")

    suffix = IMAGE_SUFFIX.get(str(content_type or "").lower())
    if suffix is None:
        raise EmojiPackError("仅支持 PNG / JPEG / GIF / WebP 图片")
    if len(content) > FACE_IMAGE_MAX_MB * 1024 * 1024:
        raise EmojiPackError(f"表情图不能超过 {FACE_IMAGE_MAX_MB}MB")
    try:
        ensure_image(content, what="表情图")
    except ImageError as e:
        raise EmojiPackError(str(e)) from e

    # 表情 id 是全局短码：从包 id 派生（u12-xiao-mao_1），跨包也不会撞
    base = manifest["id"][len(PACK_ID_PREFIX):] if manifest["id"].startswith(PACK_ID_PREFIX) else manifest["id"]
    used = {str(f.get("id") or "") for f in faces}
    n = 1
    while f"{base}_{n}" in used:
        n += 1
    face_id = f"{base}_{n}"[:40]
    file_name = f"{face_id}{suffix}"

    raw = {
        "id": face_id, "name": name, "emoji": str(emoji or "").strip(),
        "file": file_name, "caption": caption,
        "keywords": str(keywords or "").strip()[:KEYWORDS_MAX],
        "mood": str(mood or "").strip()[:MOOD_MAX],
    }
    face = catalog.emoji_face(raw)
    if face is None:
        raise EmojiPackError("表情写法不合法（名字为空或字符表情超长）")

    image_path = directory / file_name
    try:
        image_path.write_bytes(content)
        payload["faces"] = faces + [face]
        _write_json(directory / catalog.ENTRY_FILE["emojipack"], payload)
    except OSError as e:
        image_path.unlink(missing_ok=True)          # 别留一张没人引用的图
        raise EmojiPackError(f"写表情失败：{e}") from e

    catalog.invalidate_caches()
    await catalog.sync_plugins_to_db(db)
    return {**face, "pack": manifest["id"]}


async def remove_face(db, *, user_id: int, pack_id: str, face_id: str) -> None:
    """删掉一个表情（图一起删；历史消息里已经发出去的图不受影响——那是附件）"""
    manifest = _owned(pack_id, user_id)
    directory = _pack_dir(manifest)
    payload = _payload(manifest)
    faces = [f for f in (payload.get("faces") or []) if isinstance(f, dict)]
    kept = [f for f in faces if str(f.get("id") or "") != str(face_id or "")]
    if len(kept) == len(faces):
        raise EmojiPackError("这个表情不在包里")
    try:
        for face in faces:
            if str(face.get("id") or "") == str(face_id or ""):
                file_name = str(face.get("file") or "")
                if file_name:
                    (directory / Path(file_name).name).unlink(missing_ok=True)
        payload["faces"] = kept
        _write_json(directory / catalog.ENTRY_FILE["emojipack"], payload)
    except OSError as e:
        raise EmojiPackError(f"删除表情失败：{e}") from e

    catalog.invalidate_caches()


async def delete_pack(db, *, user_id: int, pack_id: str) -> None:
    """删掉整个表情包：目录交回插件卸载那条路，再把 AI 的开关记录一起清掉"""
    from sqlalchemy import delete

    from app.models.agent_emoji_pack import AgentEmojiPack

    manifest = _owned(pack_id, user_id)
    try:
        store.uninstall_plugin(manifest["id"])
    except store.PackageError as e:
        raise EmojiPackError(str(e)) from e
    # 包已经没了，AI 那边的开关记录留着只会在同名包重建时"凭空打开"
    await db.execute(delete(AgentEmojiPack).where(AgentEmojiPack.pack_id == manifest["id"]))
    await db.commit()
    catalog.invalidate_caches()
    await catalog.sync_plugins_to_db(db)
    logger.info("用户 %s 删除表情包 %s", user_id, manifest["id"])
