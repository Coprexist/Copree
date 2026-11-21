"""账本 seq 的发放：并发写同一段会话不会撞号、不会互相等到死

回归的是 2026-11-07 群 81 那次冻结：回合事务已经写进账本（未提交），插件工具自己开第二个
会话写同一段会话，用 `MAX(seq)+1` 算出一个看不见对方、却与对方相同的号 → 撞唯一索引 →
后到的等先到的提交，而先到的正等着后到的返回 → 互等 12 小时，整条 AI 回复队列停摆。

现在号由 `_reserve` 一次领走（数据库原子自增），领到多少号只取决于谁先领、不取决于谁先提交。
"""
import asyncio

import pytest

pytestmark = pytest.mark.anyio


async def _seed():
    from sqlalchemy import text
    from db_reset import clear
    from app.database import async_session

    async with async_session() as db:
        await clear(db, "agents", "users")
        await db.execute(text("""
            INSERT INTO users (id, username, password_hash, type) VALUES
            (1, '测试用户', 'x', 'human'),
            (2, '测试AI账号', 'x', 'ai')
        """))
        await db.execute(text("""
            INSERT INTO agents (id, owner_id, name, user_id, discoverable)
            VALUES (1, 1, '测试AI', 2, true)
        """))
        await db.commit()


async def test_overlapping_writers_get_distinct_seqs(migrated_db):
    """先到的写者还没提交时，后到的写者也领到自己的号：不撞、不丢、不等死"""
    from app.database import async_session
    from app.services.history import history_service as hs
    from app.utils.pure.history import make_entry

    await _seed()

    async with async_session() as first, async_session() as second:
        await hs.append(first, 1, "group:81", [
            make_entry("message", "先到的第一条", actor="user"),
            make_entry("message", "先到的第二条", actor="self"),
        ])
        # 这一批还在这笔事务里、别人看不见；第二个写者的号必须仍然排在它后面
        task = asyncio.create_task(hs.append(second, 1, "group:81", [
            make_entry("message", "后到的", actor="user"),
        ]))
        await asyncio.sleep(0.05)
        await first.commit()                     # 先到的落地，后到的随即拿到号
        written = await asyncio.wait_for(task, timeout=5)

        assert [e["seq"] for e in written] == [3], "领号看得见未提交的那两条（旧口径会算成 1 并撞车）"
        await second.commit()

        async with async_session() as check:
            assert [r["seq"] for r in await hs.read(check, 1, "group:81")] == [1, 2, 3]


async def test_clear_restarts_numbering_from_one(migrated_db):
    """解锁重写连计数一起清：重写后的账本还是从 1 开始"""
    from app.database import async_session
    from app.services.history import history_service as hs
    from app.utils.pure.history import make_entry

    await _seed()

    async with async_session() as db:
        await hs.append(db, 1, "group:81", [
            make_entry("message", f"老的{i}", actor="user") for i in range(1, 4)
        ])
        await db.commit()
        assert [r["seq"] for r in await hs.read(db, 1, "group:81")] == [1, 2, 3]

        await hs.clear(db, 1, "group:81")
        await hs.append(db, 1, "group:81", [make_entry("message", "新的1", actor="user")])
        assert [r["seq"] for r in await hs.read(db, 1, "group:81")] == [1]


async def test_first_write_after_migration_continues_from_existing_ledger(migrated_db):
    """老库升级：计数行不存在时按现有 MAX(seq) 自播种，迁移不需要回填"""
    from sqlalchemy import text
    from app.database import async_session
    from app.services.history import history_service as hs
    from app.utils.pure.history import make_entry

    await _seed()

    async with async_session() as db:
        await hs.append(db, 1, "group:81", [
            make_entry("message", f"老的{i}", actor="user") for i in range(1, 3)
        ])
        await db.commit()
        # 模拟「迁移刚跑完、还没有任何人领过号」
        await db.execute(text("DELETE FROM agent_history_seq"))
        await db.commit()

        written = await hs.append(db, 1, "group:81", [make_entry("message", "接上", actor="user")])
        assert [e["seq"] for e in written] == [3]
