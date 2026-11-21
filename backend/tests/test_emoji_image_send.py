"""图片表情 → 附件：入口归一后它就是一张普通图片，通道不再认识"表情"

2026-11-14 定的分层：字符表情走文本替换；纯图片表情摘掉标记、变成 attachments，
站内渲染与 QQ 图片消息共用同一条附件链路。
"""
import os
import tempfile

from sqlalchemy import text

PACK = {
    "id": "whale", "name": "大肥鱼", "usage": "",
    "faces": [
        {"id": "whale_deyi", "name": "得意", "emoji": "", "file": "deyi.png"},
        {"id": "whale_ok", "name": "好", "emoji": "👌", "file": ""},
    ],
}


def test_image_face_is_taken_out_of_the_text():
    from app.services.plugin.catalog import take_image_faces

    text, faces = take_image_faces("看这个 :whale_deyi: 好耶", [PACK])
    assert text == "看这个 好耶", text
    assert [f["id"] for f in faces] == ["whale_deyi"]

    # 字符表情不归这里管（上游已换成 unicode）
    text2, faces2 = take_image_faces(":whale_ok:", [PACK])
    assert text2 == ":whale_ok:" and faces2 == []

    # 代码块里的短码是在举例，不是发表情
    text3, faces3 = take_image_faces("写法是 `:whale_deyi:` 这样", [PACK])
    assert faces3 == [] and ":whale_deyi:" in text3, (text3, faces3)


async def test_emoji_asset_is_hardlinked_and_registered_once(migrated_db):
    """表情图落进 data/emoji/ 并登记成平台文件；重复调用复用同一行、同一份 inode"""
    from app.config import settings
    from app.database import async_session
    from app.models.file import FileMetadata
    from app.services.plugin import catalog

    workdir = tempfile.mkdtemp(prefix="emoji-data-")
    src_dir = tempfile.mkdtemp(prefix="emoji-src-")
    src = os.path.join(src_dir, "deyi.png")
    with open(src, "wb") as f:
        f.write(b"png-bytes")

    original_dir = settings.data_dir
    original_scan = catalog.scan_disk
    original_asset = catalog.emoji_asset_path
    settings.data_dir = workdir
    catalog.scan_disk = lambda: {"whale": {"id": "whale", "_dir": src_dir, "name": "大肥鱼"}}
    catalog.emoji_asset_path = lambda manifest, name: src
    face = {"id": "whale_deyi", "pack": "whale", "file": "deyi.png"}
    try:
        async with async_session() as db:
            first = await catalog.emoji_asset_file(db, face)
            await db.commit()
        async with async_session() as db2:
            second = await catalog.emoji_asset_file(db2, face)
            await db2.commit()
            meta = await db2.get(FileMetadata, first["file_id"])

        assert first["file_id"] == second["file_id"]
        assert meta.owner_type == "system" and int(meta.owner_id) == 0
        assert meta.path == "emoji/whale/deyi.png", meta.path
        assert os.stat(src).st_ino == os.stat(os.path.join(workdir, meta.path)).st_ino
    finally:
        settings.data_dir = original_dir
        catalog.scan_disk = original_scan
        catalog.emoji_asset_path = original_asset


async def test_group_message_with_image_face_carries_an_attachment(migrated_db):
    """写 :id: 发到群里 → 正文干净、图片进 attachments（站内与通道都靠它）"""
    from app.chat.gm import send_gm_message
    from app.database import async_session
    from app.models.file import FileMetadata
    from app.services.plugin import catalog

    async def fake_packs(db, *, cached=True):
        return [PACK]

    async def fake_asset(db, face):
        meta = FileMetadata(
            path="emoji/whale/deyi.png", owner_type="system", owner_id=0,
            size=9, mime_type="image/png", collaboration_mode="open",
        )
        db.add(meta)
        await db.flush()
        return {"file_id": int(meta.id), "name": "deyi.png", "path": meta.path,
                "size": 9, "mime_type": "image/png"}

    original_packs = catalog.enabled_emoji_packs
    original_asset = catalog.emoji_asset_file
    catalog.enabled_emoji_packs = fake_packs
    catalog.emoji_asset_file = fake_asset
    try:
        async with async_session() as db:
            from db_reset import clear

            await clear(db, "file_references", "file_metadata", "messages", "group_members", "groups", "users")
            await db.execute(text(
                "INSERT INTO users (id, username, password_hash, type) VALUES (1, '群主', 'x', 'human')"
            ))
            await db.execute(text(
                "INSERT INTO groups (id, name, owner_type, owner_id, avatar_mode, include_ai_in_avatar) "
                "VALUES (5, '测试群', 'human', 1, 'default', true)"
            ))
            await db.execute(text(
                "INSERT INTO group_members (group_id, member_type, member_id, role) "
                "VALUES (5, 'human', 1, 'owner')"
            ))
            await db.commit()

            message = await send_gm_message(
                db, group_id=5, sender_type="human", sender_id=1, content="看 :whale_deyi:",
            )
            await db.commit()

        assert message.content == "看", message.content
        assert message.attachments and message.attachments[0]["mime_type"] == "image/png"
    finally:
        catalog.enabled_emoji_packs = original_packs
        catalog.emoji_asset_file = original_asset
