"""用户自制表情包：写出来就是一个 emojipack 插件（目录 + plugin.json + emoji.json）。

这条链上有两个容易出错的边界，用例就围着它们：
1. 写与读必须是同一种形状——写进去的 emoji.json 要能被 catalog 原样读出来，
   否则「用户传了表情，AI 却看不到」；
2. 归属只认 manifest 的 owner_user_id——别人的包一个字节也不能改。

用例把用户插件目录换到临时目录（conftest 的数据根本来就是临时目录，这里再保险一层，
也才敢断言目录内容）。路由用例走 ASGI，覆盖 multipart 与鉴权。
"""
import json
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path

import httpx
from sqlalchemy import text

OWNER, OTHER = 801, 802
AGENT, AGENT_USER = 801, 8011


def _png(size=(8, 8)) -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, (30, 160, 200)).save(buf, format="PNG")
    return buf.getvalue()


SVG_AS_PNG = b'<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8"/>'


@contextmanager
def _plugin_dir():
    """用户插件目录 → 临时目录；结束后还原并清缓存（缓存不清会让下个用例看到这张牌）"""
    from app.services.plugin import catalog

    old = catalog.USER_PLUGIN_DIR
    root = Path(tempfile.mkdtemp(prefix="user-emoji-packs-"))
    catalog.USER_PLUGIN_DIR = root
    catalog.invalidate_caches()
    try:
        yield root
    finally:
        catalog.USER_PLUGIN_DIR = old
        catalog.invalidate_caches()
        shutil.rmtree(root, ignore_errors=True)


async def _seed(db) -> None:
    from db_reset import clear

    await clear(db, "users", "agents", "plugins", "agent_emoji_packs", "file_metadata")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type, role, is_active) VALUES "
        "(:owner, '表情作者', 'x', 'human', 'user', true), "
        "(:other, '路人', 'x', 'human', 'user', true), "
        "(:ai, '作者的AI账号', 'x', 'ai', 'user', true)"
    ), {"owner": OWNER, "other": OTHER, "ai": AGENT_USER})
    await db.execute(text(
        "INSERT INTO agents (id, owner_id, name, user_id) VALUES (:a, :owner, '作者的AI', :u)"
    ), {"a": AGENT, "owner": OWNER, "u": AGENT_USER})
    await db.commit()


def _token(user_id: int) -> dict:
    from app.utils.auth import create_access_token

    return {"Authorization": "Bearer " + create_access_token(
        {"user_id": user_id, "username": f"u{user_id}", "role": "user"})}


def _client(ip: str) -> httpx.AsyncClient:
    from app.main import app

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=(ip, 123)), base_url="http://test")


# ── service 层 ──────────────────────────────────────────────

async def test_create_pack_writes_a_well_formed_plugin(migrated_db):
    """建包 = 在 plugins/ 下写出一个能直接被 catalog 读到的 emojipack"""
    from app.database import async_session
    from app.models.plugin import Plugin
    from app.services.plugin import catalog, emoji_packs

    with _plugin_dir() as root:
        async with async_session() as db:
            await _seed(db)
            pack = await emoji_packs.create_pack(db, user_id=OWNER, name="My Cat", author="表情作者")
            pack_dir = root / pack["id"]

            manifest = json.loads((pack_dir / "plugin.json").read_text(encoding="utf-8"))
            assert manifest["category"] == "emojipack"
            assert manifest["owner_user_id"] == OWNER, "归属写在 manifest 上，改包只看这一项"
            assert manifest["entry"] == "emoji.json"
            assert json.loads((pack_dir / "emoji.json").read_text(encoding="utf-8"))["faces"] == []

            row = await db.get(Plugin, pack["id"])
            assert row is not None and row.enabled, "写完就是一个可用的插件"

            assert [p["id"] for p in await emoji_packs.list_mine(db, OWNER)] == [pack["id"]]
            assert await emoji_packs.list_mine(db, OTHER) == [], "别人的包不该出现在我的清单里"


async def test_add_face_demands_a_real_image_and_an_ai_readable_description(migrated_db):
    from app.database import async_session
    from app.services.plugin import catalog, emoji_packs

    with _plugin_dir():
        async with async_session() as db:
            await _seed(db)
            pack = await emoji_packs.create_pack(db, user_id=OWNER, name="My Cat")

            face = await emoji_packs.add_face(
                db, user_id=OWNER, pack_id=pack["id"], name="猫猫点头",
                caption="猫猫拼命点头，表示强烈同意", keywords="同意 点头", mood="happy",
                content=_png(), content_type="image/png",
            )
            assert face["caption"] and face["keywords"] == "同意 点头" and face["mood"] == "happy"
            assert face["file"].endswith(".png")
            assert face["id"].startswith("u801-"), "短码从包 id 派生，跨包不会撞"

            # 写进去的必须能被 catalog 读出来（同一个校验函数）
            pack_view = catalog.get_emoji_pack(catalog.scan_disk()[pack["id"]])
            assert [f["id"] for f in pack_view["faces"]] == [face["id"]]
            assert pack_view["faces"][0]["caption"] == "猫猫拼命点头，表示强烈同意"

            for kwargs, why in (
                ({"caption": ""}, "没有说明 = AI 不知道什么时候发"),
                ({"content": SVG_AS_PNG}, "谎报成 png 的非图片字节"),
                ({"content_type": "image/svg+xml"}, "SVG 不进表情包"),
            ):
                data = dict(user_id=OWNER, pack_id=pack["id"], name="再来一张",
                            caption="说明", content=_png(), content_type="image/png")
                data.update(kwargs)
                try:
                    await emoji_packs.add_face(db, **data)
                except emoji_packs.EmojiPackError:
                    pass
                else:
                    raise AssertionError(f"应当拒绝：{why}")


async def test_face_shortcode_becomes_an_image_attachment(migrated_db):
    """自制包要能走完整条链：勾给 AI → 正文写短码 → 变图片附件"""
    from app.database import async_session
    from app.services.plugin import catalog, emoji_packs

    with _plugin_dir():
        async with async_session() as db:
            await _seed(db)
            pack = await emoji_packs.create_pack(db, user_id=OWNER, name="My Cat")
            face = await emoji_packs.add_face(
                db, user_id=OWNER, pack_id=pack["id"], name="点头",
                caption="点头同意", content=_png(), content_type="image/png",
            )
            await catalog.set_agent_pack(db, AGENT, pack["id"], True)

            packs = await catalog.packs_for_agent(db, AGENT)
            assert any(p["id"] == pack["id"] for p in packs), "勾上之后这个 AI 才拿到包"

            text_out, images = await catalog.take_image_emoji_attachments(db, f"好 {':' + face['id'] + ':'}", packs)
            assert text_out == "好"
            assert images and images[0]["mime_type"] == "image/png"


async def test_only_the_owner_may_touch_a_pack(migrated_db):
    from app.database import async_session
    from app.services.plugin import emoji_packs

    with _plugin_dir():
        async with async_session() as db:
            await _seed(db)
            pack = await emoji_packs.create_pack(db, user_id=OWNER, name="My Cat")
            for call in (
                lambda: emoji_packs.add_face(db, user_id=OTHER, pack_id=pack["id"], name="偷",
                                            caption="偷图", content=_png(), content_type="image/png"),
                lambda: emoji_packs.delete_pack(db, user_id=OTHER, pack_id=pack["id"]),
            ):
                try:
                    await call()
                except emoji_packs.EmojiPackError:
                    continue
                raise AssertionError("别人的包不该能改")


async def test_delete_face_and_pack_clean_up_everything(migrated_db):
    from app.database import async_session
    from app.services.plugin import catalog, emoji_packs

    with _plugin_dir() as root:
        async with async_session() as db:
            await _seed(db)
            pack = await emoji_packs.create_pack(db, user_id=OWNER, name="My Cat")
            pack_dir = root / pack["id"]
            face = await emoji_packs.add_face(
                db, user_id=OWNER, pack_id=pack["id"], name="点头",
                caption="点头同意", content=_png(), content_type="image/png",
            )
            image = pack_dir / face["file"]
            assert image.is_file()

            await emoji_packs.remove_face(db, user_id=OWNER, pack_id=pack["id"], face_id=face["id"])
            assert not image.exists(), "删表情要连图一起删"
            payload = json.loads((pack_dir / "emoji.json").read_text(encoding="utf-8"))
            assert payload["faces"] == []

            await catalog.set_agent_pack(db, AGENT, pack["id"], True)
            await emoji_packs.delete_pack(db, user_id=OWNER, pack_id=pack["id"])
            assert not pack_dir.exists()
            assert pack["id"] not in catalog.scan_disk()
            prefs = await catalog.agent_pack_prefs(db, AGENT)
            assert prefs == {}, "包没了，AI 的开关记录要一起清掉（否则同名包重建会凭空打开）"


# ── 路由层 ─────────────────────────────────────────────────

async def test_api_creates_uploads_and_manages_my_packs(migrated_db):
    from app.database import async_session
    from app.services.plugin import catalog

    with _plugin_dir():
        async with async_session() as db:
            await _seed(db)

        headers = _token(OWNER)
        async with _client("198.18.0.31") as client:
            created = await client.post("/emoji-packs", headers=headers, json={"name": "API 猫"})
            assert created.status_code == 200, created.text
            pack_id = created.json()["id"]

            empty_name = await client.post("/emoji-packs", headers=headers, json={"name": "  "})
            assert empty_name.status_code == 400, "名字不能是空白"

            uploaded = await client.post(
                f"/emoji-packs/{pack_id}/faces", headers=headers,
                data={"name": "点头", "caption": "猫猫点头同意", "keywords": "同意", "mood": "happy"},
                files={"file": ("cat.png", _png(), "image/png")},
            )
            assert uploaded.status_code == 200, uploaded.text
            face_id = uploaded.json()["id"]

            mine = await client.get("/emoji-packs/mine", headers=headers)
            assert mine.status_code == 200, mine.text
            body = mine.json()
            assert [p["id"] for p in body["packs"]] == [pack_id]
            assert body["packs"][0]["faces"][0]["caption"] == "猫猫点头同意"
            assert body["limits"]["faces_max"] > 0

            # 别人的 token 改同一个包：一律拒
            stolen = await client.post(
                f"/emoji-packs/{pack_id}/faces", headers=_token(OTHER),
                data={"name": "偷", "caption": "偷图"},
                files={"file": ("cat.png", _png(), "image/png")},
            )
            assert stolen.status_code == 400, stolen.text

            # AI 开关接口：包出现在这只 AI 的清单里，勾上后 packs_for_agent 生效
            listed = await client.get(f"/agents/{AGENT}/emoji-packs", headers=headers)
            row = next(p for p in listed.json()["packs"] if p["id"] == pack_id)
            assert row["enabled"] is False, "默认白名单：没勾 = 不用"

            turned_on = await client.put(
                f"/agents/{AGENT}/emoji-packs/{pack_id}", headers=headers, json={"enabled": True},
            )
            assert turned_on.status_code == 200, turned_on.text
            async with async_session() as db:
                assert any(p["id"] == pack_id for p in await catalog.packs_for_agent(db, AGENT))

            removed_face = await client.delete(f"/emoji-packs/{pack_id}/faces/{face_id}", headers=headers)
            assert removed_face.status_code == 200, removed_face.text

            removed = await client.delete(f"/emoji-packs/{pack_id}", headers=headers)
            assert removed.status_code == 200, removed.text
            assert (await client.get("/emoji-packs/mine", headers=headers)).json()["packs"] == []
