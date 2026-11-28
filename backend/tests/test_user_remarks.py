"""用户备注：群/人一个对象至多一条，空串即删除，列表按备注优先显示

备注是"我对这个对象的称呼"：只对我可见、删群删账号时跟着走（外键在库上）。
"""
from sqlalchemy import text

OWNER, OTHER, PEER = 1, 2, 3
GROUP = 5


async def _seed(db):
    from db_reset import clear

    await clear(db, "user_remarks", "groups", "users")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES "
        "(:o, '我', 'x', 'human'), (:t, '别人', 'x', 'human')"
    ), {"o": OWNER, "t": OTHER})
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES (:p, '对端', 'x', 'human')"
    ), {"p": PEER})
    await db.execute(text(
        "INSERT INTO groups (id, name, owner_type, owner_id, avatar_mode, include_ai_in_avatar) "
        "VALUES (:g, '测试群', 'human', :o, 'default', true)"
    ), {"g": GROUP, "o": OWNER})
    await db.commit()


async def test_set_and_list_a_group_remark(migrated_db):
    from app.database import async_session
    from app.services.social import remark_service

    async with async_session() as db:
        await _seed(db)
        saved = await remark_service.set_remark(db, OWNER, "group", GROUP, "  书友会  ")
        assert saved == {"target_type": "group", "target_id": GROUP, "remark": "书友会"}
        assert await remark_service.list_remarks(db, OWNER) == [saved]


async def test_remark_is_per_owner(migrated_db):
    """我给谁设备注只影响我：别人的列表里没有。"""
    from app.database import async_session
    from app.services.social import remark_service

    async with async_session() as db:
        await _seed(db)
        await remark_service.set_remark(db, OWNER, "group", GROUP, "只有我能看见")
        assert await remark_service.list_remarks(db, OTHER) == []


async def test_rewrite_replaces_the_only_row(migrated_db):
    """改名是覆盖，不是再加一条（唯一键在库上）。"""
    from app.database import async_session
    from app.services.social import remark_service

    async with async_session() as db:
        await _seed(db)
        await remark_service.set_remark(db, OWNER, "group", GROUP, "第一版")
        await remark_service.set_remark(db, OWNER, "group", GROUP, "第二版")
        rows = await remark_service.list_remarks(db, OWNER)
        assert rows == [{"target_type": "group", "target_id": GROUP, "remark": "第二版"}]


async def test_empty_remark_deletes_it(migrated_db):
    from app.database import async_session
    from app.services.social import remark_service

    async with async_session() as db:
        await _seed(db)
        await remark_service.set_remark(db, OWNER, "group", GROUP, "临时")
        assert await remark_service.set_remark(db, OWNER, "group", GROUP, "   ") is None
        assert await remark_service.list_remarks(db, OWNER) == []


async def test_user_remark_and_group_remark_do_not_collide(migrated_db):
    """同一个 id 既可能是群也可能是人：两条备注各存各的。"""
    from app.database import async_session
    from app.services.social import remark_service

    async with async_session() as db:
        await _seed(db)
        await remark_service.set_remark(db, OWNER, "group", GROUP, "群备注")
        await remark_service.set_remark(db, OWNER, "user", PEER, "人备注")
        got = await remark_service.remarks_map(db, OWNER)
        assert got[("group", GROUP)] == "群备注"
        assert got[("user", PEER)] == "人备注"


async def test_remark_is_trimmed_and_capped(migrated_db):
    from app.database import async_session
    from app.services.social import remark_service

    async with async_session() as db:
        await _seed(db)
        saved = await remark_service.set_remark(db, OWNER, "user", PEER, "长" * 80)
        assert len(saved["remark"]) == remark_service.REMARK_MAX_CHARS


async def test_unknown_target_is_rejected(migrated_db):
    from app.database import async_session
    from app.services.social import remark_service

    async with async_session() as db:
        await _seed(db)
        for target_type, target_id in (("group", 999), ("user", 999), ("planet", 1)):
            try:
                await remark_service.set_remark(db, OWNER, target_type, target_id, "x")
            except ValueError:
                continue
            raise AssertionError(f"{target_type}/{target_id} 应该被拒绝")


async def test_cannot_remark_yourself(migrated_db):
    from app.database import async_session
    from app.services.social import remark_service

    async with async_session() as db:
        await _seed(db)
        try:
            await remark_service.set_remark(db, OWNER, "user", OWNER, "我自己")
        except ValueError:
            return
        raise AssertionError("给自己设备注没有意义，应该被拒绝")
