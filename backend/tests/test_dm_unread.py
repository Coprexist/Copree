"""私信「未读」的单一真相：dm_messages.read_at（用户 2026-09-23）。

本来打算给 pending_messages 加私信支持（文档 §4.3 的模型里有 dm_session_id），但私信
已经有 read_at 在管「对方看没看」：对方打开会话、AI 回复（send_dm_message 的「回复即阅读」）
都会把它标上。再存一份暂存 = 同一件事两处真相、迟早对不上。
所以走「读出来」：不加表、不加迁移，pending_messages 只记群聊那条投递链的账。
"""
from sqlalchemy import text


async def _seed():
    from app.database import async_session

    async with async_session() as db:
        from db_reset import clear
        await clear(db, "dm_messages", "dm_sessions", "agents", "users")
        await db.execute(text(
            "INSERT INTO users (id, username, password_hash, type) VALUES "
            "(1, '人类', 'x', 'human'), (41, '浮生', 'x', 'ai')"
        ))
        await db.execute(text(
            "INSERT INTO agents (id, owner_id, name, user_id, discoverable) VALUES "
            "(25, 1, '浮生（人物志1）', 41, true)"
        ))
        await db.execute(text(
            "INSERT INTO dm_sessions (session_id, user1_id, user2_id) VALUES ('1_41', 1, 41)"
        ))
        await db.execute(text(
            "INSERT INTO dm_messages (session_id, sender_id, content, message_type, read_at, created_at) VALUES "
            "('1_41', 1, '@浮生 你好', 'normal', NULL, '2026-09-23 10:00:00'), "
            "('1_41', 1, '在吗', 'normal', NULL, '2026-09-23 10:01:00'), "
            "('1_41', 41, '我在', 'normal', NULL, '2026-09-23 10:02:00')"
        ))
        await db.commit()


async def test_unread_dm_is_visible_to_the_ai(migrated_db):
    from app.chat.delivery import check_unread_dms
    from app.database import async_session

    await _seed()
    async with async_session() as db:
        rows = await check_unread_dms(db, 25)
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["session_id"] == "1_41" and row["peer_name"] == "人类"
    assert row["unread_count"] == 2, "只算对方发来的：AI 自己发的不算未读"
    assert row["last_message_preview"] == "在吗"
    assert row["last_message_at"]


async def test_ai_reply_clears_it(migrated_db):
    """AI 回一句 → 「回复即阅读」把对方的消息标掉 → 未读自己消失（不需要第二套账本）。"""
    from app.chat.delivery import check_unread_dms
    from app.chat.dm import send_dm_message
    from app.database import async_session

    await _seed()
    async with async_session() as db:
        await send_dm_message(db, "1_41", sender_id=41, content="在的", skip_friendship_check=True)
        await db.commit()

    async with async_session() as db:
        assert await check_unread_dms(db, 25) == []


async def test_dm_does_not_write_pending_rows(migrated_db):
    """设计选择：私信不往 pending_messages 记账（那里只放群聊积压）。"""
    from app.chat.dm import send_dm_message
    from app.database import async_session

    await _seed()
    async with async_session() as db:
        await send_dm_message(db, "1_41", sender_id=1, content="喂", skip_friendship_check=True)
        await db.commit()

    async with async_session() as db:
        n = (await db.execute(text("select count(*) from pending_messages"))).scalar()
    assert n == 0


async def test_human_peer_has_nothing_to_report(migrated_db):
    """AI 视角：跟人类之间没有「我未读」这回事（未读是收件人的账）。"""
    from app.chat.delivery import check_unread_dms
    from app.database import async_session

    await _seed()
    async with async_session() as db:
        # agent 25 是 user 41；人类 user 1 没有 agent → 查不到任何会话
        assert await check_unread_dms(db, 999) == []
