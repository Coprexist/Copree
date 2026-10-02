"""头像上传：只信上传方声明的 content-type 是不够的，字节本身要能被解开。

把 SVG（或任意文件）谎报成 image/png 曾能一路通过——compress_avatar /
make_avatar_thumbnail 解不开时会把原字节原样退回，于是接口 200、落盘一个打不开的 .jpg
（界面上就是裂图），而旧头像文件在写新文件之前已经被删掉了。三条入口（用户 / AI / 群聊）
现在共用 utils/avatar_upload.read_avatar：类型、大小、真伪一起判，且都在覆盖旧头像之前完成。
"""
import io
import tempfile
from pathlib import Path

import httpx
from sqlalchemy import text

from app.utils.avatar_upload import DECODE_ERROR, TYPE_ERROR

# 谎报成 image/png 的 SVG：能过类型白名单，但 Pillow 解不开
SVG_AS_PNG = b'<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8"><circle cx="4" cy="4" r="3"/></svg>'
OLD_AVATAR = "/api/fs/download-avatar/user_901_old.png"


def _png(size=(4, 4)) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(buf, format="PNG")
    return buf.getvalue()


def _avatar_dir() -> Path:
    """头像落盘改到临时目录：用例不许往真实 uploads/avatars 里写东西"""
    from app.config import settings

    path = Path(tempfile.mkdtemp(prefix="avatar-test-"))
    settings.avatars_dir = str(path)
    return path


def _client(ip: str) -> httpx.AsyncClient:
    # 每个用例换一个来源 IP：凭证配额按来源算，共用会让用例互相吃配额
    from app.main import app

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=(ip, 123)), base_url="http://test")


async def _seed() -> None:
    """一个人类用户 + 他名下的 AI + 他建的群：三处头像入口都要能过鉴权"""
    from app.database import async_session

    async with async_session() as db:
        from db_reset import clear

        await clear(db, "users", "agents", "groups")
        await db.execute(text(
            "INSERT INTO users (id, username, password_hash, type, role, is_active) "
            "VALUES (901, 'avatar-probe', 'x', 'human', 'user', true)"))
        await db.execute(text(
            "INSERT INTO agents (id, owner_id, name, user_id) VALUES (901, 901, '头像探针AI', 901)"))
        await db.execute(text(
            "INSERT INTO groups (id, name, owner_type, owner_id, avatar_mode, include_ai_in_avatar) "
            "VALUES (901, '头像探针群', 'human', 901, 'default', true)"))
        await db.execute(text(
            "INSERT INTO group_members (group_id, member_type, member_id, role) "
            "VALUES (901, 'human', 901, 'owner')"))
        await db.commit()


def _token() -> dict:
    from app.utils.auth import create_access_token

    return {"Authorization": "Bearer " + create_access_token(
        {"user_id": 901, "username": "avatar-probe", "role": "user"})}


async def test_user_avatar_rejects_fake_images_and_keeps_the_old_one(migrated_db):
    """坏字节要当场拒，而且不能碰旧头像（旧头像被删就等于把人家的头像弄丢了）"""
    from app.database import async_session

    tmp = _avatar_dir()
    await _seed()
    headers = _token()

    old = tmp / "user_901_old.png"
    old.write_bytes(_png())
    async with async_session() as db:
        await db.execute(text("UPDATE users SET avatar_url = :u WHERE id = 901"), {"u": OLD_AVATAR})
        await db.commit()

    async with _client("198.18.0.21") as client:
        fake = await client.post("/user/avatar", headers=headers,
                                 files={"file": ("evil.png", SVG_AS_PNG, "image/png")})
        assert fake.status_code == 400 and fake.json()["detail"] == DECODE_ERROR, fake.text
        assert old.exists(), "校验没过却把旧头像删了"

        wrong_type = await client.post("/user/avatar", headers=headers,
                                       files={"file": ("a.txt", _png(), "text/plain")})
        assert wrong_type.status_code == 400 and wrong_type.json()["detail"] == TYPE_ERROR, wrong_type.text
        assert old.exists()

        ok = await client.post("/user/avatar", headers=headers,
                               files={"file": ("ok.png", _png((8, 8)), "image/png")})
        assert ok.status_code == 200, ok.text
        url = ok.json()["avatar_url"]

    async with async_session() as db:
        stored = (await db.execute(text("SELECT avatar_url FROM users WHERE id = 901"))).scalar()
    # 无透明通道的 PNG 会被压成 JPEG，扩展名随之变——只要求指针与返回一致、文件真能打开
    assert stored == url and url.rsplit(".", 1)[-1] in ("jpg", "png"), (stored, url)

    from PIL import Image

    with Image.open(tmp / url.rsplit("/", 1)[-1]) as img:
        assert img.size == (8, 8), "落盘的必须是一张真能打开的图"


async def test_agent_avatar_rejects_fake_images(migrated_db):
    """AI 头像同一套校验：坏的拒掉且不留文件"""
    tmp = _avatar_dir()
    await _seed()

    async with _client("198.18.0.22") as client:
        fake = await client.post("/agents/901/avatar", headers=_token(),
                                 files={"file": ("evil.png", SVG_AS_PNG, "image/png")})
        assert fake.status_code == 400 and fake.json()["detail"] == DECODE_ERROR, fake.text

    assert not list(tmp.glob("agent_901*")), "被拒的上传不该留下任何文件"


async def test_group_avatar_rejects_fake_images_and_oversized_ones(migrated_db):
    """群头像原先连类型白名单都没有：坏字节、超大文件都要在写盘前拦下"""
    tmp = _avatar_dir()
    await _seed()

    async with _client("198.18.0.23") as client:
        fake = await client.post("/groups/901/avatar", headers=_token(),
                                 files={"file": ("evil.png", SVG_AS_PNG, "image/png")})
        assert fake.status_code == 400 and fake.json()["detail"] == DECODE_ERROR, fake.text

        huge = await client.post("/groups/901/avatar", headers=_token(),
                                 files={"file": ("huge.png", b"\x89PNG" + b"\x00" * (6 * 1024 * 1024), "image/png")})
        assert huge.status_code == 400 and "5MB" in huge.json()["detail"], huge.text

    assert not list(tmp.glob("group_901*"))
