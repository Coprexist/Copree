"""转发引用必须遵守"同一引用方对同一文件只留一条记录"（设计文档《文件存储与协作系统》§5.3）

2026-11-09 线上：先把文件预览一遍（落一行 read），再点转发——track_forward_reference
自己 INSERT 一行 forward，撞 (file_id, referrer_type, referrer_id) 唯一键 → 500，
整个发送事务回滚。结果是界面报"已发送"，对方聊天里根本没有这条消息。
"""
from sqlalchemy import text

USER_ID = 903
FILE_ID = 990183
OWNER_AI = 120


async def _seed():
    from app.database import async_session

    async with async_session() as db:
        await db.execute(text(
            "INSERT INTO file_metadata (id, path, owner_type, owner_id, size, mime_type, collaboration_mode) "
            f"VALUES ({FILE_ID}, 'attachments/probe.md', 'ai', {OWNER_AI}, 12, 'text/markdown', 'open') "
            "ON CONFLICT (id) DO NOTHING"
        ))
        await db.execute(text("DELETE FROM file_references WHERE file_id = :f"), {"f": FILE_ID})
        await db.commit()


async def _ref_types() -> list[str]:
    from app.database import async_session

    async with async_session() as db:
        rows = await db.execute(text(
            "SELECT ref_type FROM file_references "
            "WHERE file_id = :f AND referrer_type = 'human' AND referrer_id = :u"
        ), {"f": FILE_ID, "u": USER_ID})
        return list(rows.scalars().all())


async def test_forward_after_preview_upgrades_the_same_row(migrated_db):
    """预览过再转发：不该炸，也不该多出一行——那一行就地升级成 forward"""
    from app.database import async_session
    from app.services.content.file_service import track_file_reference, track_forward_reference

    await _seed()
    async with async_session() as db:
        await track_file_reference(db, FILE_ID, "human", USER_ID, "read")
        await db.commit()
    assert await _ref_types() == ["read"]

    async with async_session() as db:
        assert await track_forward_reference(db, FILE_ID, "human", USER_ID) is True
        await db.commit()
    assert await _ref_types() == ["forward"]


async def test_a_later_preview_does_not_downgrade_a_forward(migrated_db):
    """转发之后再预览：ref_type 只升不降，否则配额会被悄悄还回去"""
    from app.database import async_session
    from app.services.content.file_service import track_file_reference, track_forward_reference

    await _seed()
    async with async_session() as db:
        await track_forward_reference(db, FILE_ID, "human", USER_ID)
        await track_file_reference(db, FILE_ID, "human", USER_ID, "read")
        await db.commit()
    assert await _ref_types() == ["forward"]


async def test_forwarding_twice_is_idempotent(migrated_db):
    """同一人重复转发同一个文件：还是那一行，不新增、不报错"""
    from app.database import async_session
    from app.services.content.file_service import track_forward_reference

    await _seed()
    async with async_session() as db:
        await track_forward_reference(db, FILE_ID, "human", USER_ID)
        await track_forward_reference(db, FILE_ID, "human", USER_ID)
        await db.commit()
    assert await _ref_types() == ["forward"]


async def test_owner_forwarding_own_file_records_nothing(migrated_db):
    """自己的文件不记转发引用（否则等于给自己扣配额）"""
    from app.database import async_session
    from app.services.content.file_service import track_forward_reference

    await _seed()
    async with async_session() as db:
        assert await track_forward_reference(db, FILE_ID, "ai", OWNER_AI) is False
        await db.commit()
    assert await _ref_types() == []
