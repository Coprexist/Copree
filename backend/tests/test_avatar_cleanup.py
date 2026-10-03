"""无引用头像的清理：搬进回收站，不许直接删

头像变"无引用"常常只是一个瞬间的状态（上传失败的中间态、指针被清空但原图还在）。
旧的上传路径就是"先删旧文件、再校验新文件"，解码失败时原图已经没了、不可恢复；
清理这道关口是最后一道会真删文件的地方，所以卡住它。
"""
import os
import tempfile
import time
from pathlib import Path

import httpx
from sqlalchemy import text

STALE_DAYS = 30


def _avatar_dir() -> Path:
    """头像落盘改到临时目录：用例不许往真实 uploads/avatars 里写东西"""
    from app.config import settings

    path = Path(tempfile.mkdtemp(prefix="avatar-clean-"))
    settings.avatars_dir = str(path)
    return path


def _client(ip: str) -> httpx.AsyncClient:
    from app.main import app

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=(ip, 123)), base_url="http://test")


def _token() -> dict:
    from app.utils.auth import create_access_token

    return {"Authorization": "Bearer " + create_access_token(
        {"user_id": 902, "username": "cleanup-admin", "role": "admin"})}


async def _seed_admin() -> None:
    from app.database import async_session

    async with async_session() as db:
        await db.execute(text("DELETE FROM users WHERE id = 902"))
        await db.execute(text(
            "INSERT INTO users (id, username, password_hash, type, role, is_active) "
            "VALUES (902, 'cleanup-admin', 'x', 'human', 'admin', true)"))
        await db.commit()


async def test_unreferenced_avatar_goes_to_trash_instead_of_being_deleted(migrated_db):
    d = _avatar_dir()
    await _seed_admin()

    stale_file = d / "user_902_old.png"
    stale_file.write_bytes(b"old-avatar")
    old_time = time.time() - STALE_DAYS * 86400
    os.utime(stale_file, (old_time, old_time))

    fresh_file = d / "user_902_fresh.png"
    fresh_file.write_bytes(b"fresh-avatar")

    async with _client("198.18.0.31") as client:
        resp = await client.post("/admin/cleanup/files", headers=_token())
    assert resp.status_code == 200, resp.text
    assert resp.json()["cleaned_files"] == 1, resp.text

    assert not stale_file.exists(), "久未引用又过了反悔期的，该搬走"
    assert fresh_file.exists(), "刚动过的文件不能碰（那边可能正在写）"

    trash = Path(str(d) + "_trash")
    names = os.listdir(trash) if trash.is_dir() else []
    assert any(name.endswith("user_902_old.png") for name in names), f"搬走的文件要能在回收站里找回：{names}"
