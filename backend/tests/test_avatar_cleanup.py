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

    # 清理只认「库里还有没有引用」：先给 user 902 挂一张还在用的头像，
    # 否则会撞上「引用集为空即拒绝」的安全阀（那是另一条用例）
    keep_file = d / "user_902_keep.png"
    keep_file.write_bytes(b"keep-avatar")
    os.utime(keep_file, (old_time, old_time))
    from app.database import async_session
    async with async_session() as db:
        await db.execute(text(
            "UPDATE users SET avatar_url = :u WHERE id = 902"),
            {"u": "/api/fs/download-avatar/user_902_keep.png"})
        await db.commit()

    async with _client("198.18.0.31") as client:
        resp = await client.post("/admin/cleanup/files", headers=_token())
    assert resp.status_code == 200, resp.text
    assert resp.json()["cleaned_files"] == 1, resp.text

    assert not stale_file.exists(), "久未引用又过了反悔期的，该搬走"
    assert fresh_file.exists(), "刚动过的文件不能碰（那边可能正在写）"

    trash = Path(str(d) + "_trash")
    names = os.listdir(trash) if trash.is_dir() else []
    assert any(name.endswith("user_902_old.png") for name in names), f"搬走的文件要能在回收站里找回：{names}"
    assert keep_file.exists(), "库里还挂着的头像不能动"


async def test_refuses_when_no_avatar_is_referenced(migrated_db):
    """引用集为空 = 这次清理看错了库（测试库/空库），不是「大家都没头像」：
    这时一张都不能搬——2026-10-23 就是这样把 37 张真实头像整批搬进回收站的。"""
    d = _avatar_dir()
    await _seed_admin()

    from app.database import async_session
    async with async_session() as db:
        await db.execute(text("UPDATE users SET avatar_url = NULL"))
        await db.execute(text("UPDATE agents SET avatar_url = NULL"))
        await db.execute(text("UPDATE groups SET avatar_url = NULL"))
        await db.commit()

    stale_file = d / "user_902_orphan.png"
    stale_file.write_bytes(b"orphan")
    old_time = time.time() - STALE_DAYS * 86400
    os.utime(stale_file, (old_time, old_time))

    async with _client("198.18.0.32") as client:
        resp = await client.post("/admin/cleanup/files", headers=_token())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("refused") is True, body
    assert body["cleaned_files"] == 0, body
    assert stale_file.exists(), "引用集为空时一个都不能搬，宁可这次不清理"
