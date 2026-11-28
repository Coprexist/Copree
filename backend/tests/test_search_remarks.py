"""搜索按备注命中：备注是私有称呼，只对设备注的人生效

搜「老王」要能搜到真名张三的人——用户记的是自己起的称呼；
但换成别人搜同一个词，结果必须是空的（否则备注就成了泄露渠道）。
"""
from sqlalchemy import text

OWNER, OTHER, PEER = 1, 2, 3
GROUP = 5


async def _seed(db):
    from db_reset import clear

    await clear(db, "user_remarks", "groups", "users")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type, is_active) VALUES "
        "(:o, '我', 'x', 'human', true), (:t, '别人', 'x', 'human', true)"
    ), {"o": OWNER, "t": OTHER})
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type, is_active) VALUES (:p, '张三', 'x', 'human', true)"
    ), {"p": PEER})
    await db.execute(text(
        "INSERT INTO groups (id, name, owner_type, owner_id, avatar_mode, include_ai_in_avatar, searchable) "
        "VALUES (:g, '读书群', 'human', :o, 'default', true, true)"
    ), {"g": GROUP, "o": OWNER})
    await db.commit()


def _repo(db):
    from app.repositories.search_repo import SQLAlchemySearchRepository

    return SQLAlchemySearchRepository(db)


async def test_search_user_by_my_remark(migrated_db):
    from app.database import async_session
    from app.services.social import remark_service
    from app.services.social.search_service import search_entities

    async with async_session() as db:
        await _seed(db)
        await remark_service.set_remark(db, OWNER, "user", PEER, "老王")

        hits = await search_entities(_repo(db), "老王", OWNER)
        assert [row["id"] for row in hits] == [PEER]
        # 原名依然可搜：加了备注不等于改了名字
        assert [row["id"] for row in await search_entities(_repo(db), "张三", OWNER)] == [PEER]


async def test_remark_is_not_searchable_by_others(migrated_db):
    from app.database import async_session
    from app.services.social import remark_service
    from app.services.social.search_service import search_entities

    async with async_session() as db:
        await _seed(db)
        await remark_service.set_remark(db, OWNER, "user", PEER, "老王")

        assert await search_entities(_repo(db), "老王", OTHER) == []


async def test_search_group_by_my_remark(migrated_db):
    from app.database import async_session
    from app.services.social import remark_service
    from app.services.social.search_service import search_groups

    async with async_session() as db:
        await _seed(db)
        await remark_service.set_remark(db, OWNER, "group", GROUP, "书友会")

        hits = await search_groups(_repo(db), "书友会", OWNER)
        assert [row["id"] for row in hits] == [GROUP]
        assert await search_groups(_repo(db), "书友会", OTHER) == []


async def test_remark_does_not_bypass_group_searchable_switch(migrated_db):
    from app.database import async_session
    from app.services.social import remark_service
    from app.services.social.search_service import search_groups

    async with async_session() as db:
        await _seed(db)
        await remark_service.set_remark(db, OWNER, "group", GROUP, "书友会")
        await db.execute(text("UPDATE groups SET searchable = false WHERE id = :g"), {"g": GROUP})
        await db.commit()

        # 群主没开「可被搜索」，备注不能变成绕过开关的入口
        assert await search_groups(_repo(db), "书友会", OWNER) == []
