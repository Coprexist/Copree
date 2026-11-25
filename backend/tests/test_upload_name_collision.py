"""同名附件不能互相覆盖（2026-11-25 线上：网页连发两张只保住最后一张）。

规则（用户定）：
- 同名**内容相同** → 复用同一行（同一张图重复发送仍然是合并成一份）；
- 同名**内容不同** → 按序加后缀（`image (2).jpg`），绝不覆盖已有文件。

为什么不能简单加哈希：文件名是用户看得到的，哈希会让「同一张图重发」也变成新名字、
丢掉合并；而按序后缀只在真正不同时才出现，界面上的原文件名还能回流到 attachments.name。
"""
import pytest
from sqlalchemy import text

pytestmark = pytest.mark.anyio

HUMAN = 1


async def _seed(db):
    from db_reset import clear

    await clear(db, "file_metadata", "users")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES "
        "(:h, '主人', 'x', 'human')"
    ), {"h": HUMAN})
    await db.commit()


async def test_same_name_different_content_gets_a_suffix(migrated_db):
    from app.database import async_session
    from app.services.content.file_service import upload_file

    async with async_session() as db:
        await _seed(db)
        # 每个用例一个自己的目录：测试库的 DATA_DIR 是会话级临时目录，
        # 只清 DB 不清磁盘，共用目录会让上一个用例留下的 image (2).jpg 干扰序号
        first = await upload_file(db, "users/1/chat/case1", "image.jpg", b"A" * 100,
                                  "image/jpeg", "human", HUMAN)
        await db.commit()
        second = await upload_file(db, "users/1/chat/case1", "image.jpg", b"B" * 200,
                                   "image/jpeg", "human", HUMAN)
        await db.commit()

    assert first.path != second.path, "同名不同内容必须换名，不能覆盖"
    assert " (2).jpg" in second.path, second.path


async def test_same_name_same_content_is_still_merged(migrated_db):
    """同一张图重复发（同名同内容）→ 复用同一行，不能因为防覆盖把合并也弄丢。"""
    from app.database import async_session
    from app.services.content.file_service import upload_file

    async with async_session() as db:
        await _seed(db)
        first = await upload_file(db, "users/1/chat/case2", "image.jpg", b"A" * 100,
                                  "image/jpeg", "human", HUMAN)
        await db.commit()
        again = await upload_file(db, "users/1/chat/case2", "image.jpg", b"A" * 100,
                                  "image/jpeg", "human", HUMAN)
        await db.commit()

    assert again.id == first.id
    assert again.path == first.path


async def test_second_collision_keeps_counting(migrated_db):
    """已有 `image (2).jpg` 时，第三张不同的图不能又占 (2)——按序找第一个空的。"""
    from app.database import async_session
    from app.services.content.file_service import upload_file

    async with async_session() as db:
        await _seed(db)
        await upload_file(db, "users/1/chat/case3", "image.jpg", b"A" * 10, "image/jpeg", "human", HUMAN)
        await upload_file(db, "users/1/chat/case3", "image.jpg", b"B" * 10, "image/jpeg", "human", HUMAN)
        third = await upload_file(db, "users/1/chat/case3", "image.jpg", b"C" * 10, "image/jpeg", "human", HUMAN)
        await db.commit()

    assert " (3).jpg" in third.path, third.path
