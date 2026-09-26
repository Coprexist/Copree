"""AI 抄回来的上下文标记要收掉（用户 2026-09-25 报：消息末尾显示 [msg_id=2477]）。

那个标记是 format_message 加在每条消息尾部的（给 AI 认"要引用哪条"），不是给人看的。
模型偶尔把它当"回复语法"抄进自己的正文——私信里实测 4 条，用户就看到一串噪音。
入口收掉，并当成它的本意：reply_to。真库上验"收掉 + 真的引用了那条"。
"""
from app.utils.text import take_trailing_msg_id


def test_take_trailing_msg_id():
    assert take_trailing_msg_id("我都在。 [msg_id=2046]") == ("我都在。", 2046)
    assert take_trailing_msg_id("[msg_id=2046]") == ("", 2046)
    assert take_trailing_msg_id("我都在。") == ("我都在。", None)
    # 只认末尾那一个：正文中间提到的数字不动
    assert take_trailing_msg_id("见 [msg_id=12] 那条") == ("见 [msg_id=12] 那条", None)
    assert take_trailing_msg_id("") == ("", None)


async def _seed_group(db):
    from sqlalchemy import text

    from db_reset import clear

    await clear(db, "pending_messages", "messages", "group_members", "groups", "agents", "users")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES "
        "(1, '群主', 'x', 'human'), (2, '浮生', 'x', 'ai')"
    ))
    await db.execute(text(
        "INSERT INTO groups (id, name, owner_type, owner_id, avatar_mode, include_ai_in_avatar) "
        "VALUES (59, 'CoExisten', 'human', 1, 'default', true)"
    ))
    await db.execute(text(
        "INSERT INTO group_members (group_id, member_type, member_id, role) VALUES "
        "(59, 'human', 1, 'owner'), (59, 'ai', 2, 'member')"
    ))
    await db.commit()


async def test_group_entry_strips_marker_and_keeps_the_intent(migrated_db):
    from app.chat.gm import send_gm_message
    from app.database import async_session

    async with async_session() as db:
        await _seed_group(db)
        first = await send_gm_message(db, group_id=59, sender_type="human", sender_id=1, content="在吗")
        await db.commit()

        reply = await send_gm_message(
            db, group_id=59, sender_type="ai", sender_id=2, content=f"在的 [msg_id={first.id}]"
        )
        await db.commit()
        assert reply.content == "在的", reply.content          # 标记不进正文
        assert reply.reply_to == first.id, reply.reply_to      # 但它的本意被接住了

        # 瞎编的号（本群没有这条消息）→ 只收标记，不引用
        bogus = await send_gm_message(
            db, group_id=59, sender_type="ai", sender_id=2, content="嗯 [msg_id=999999]"
        )
        await db.commit()
        assert bogus.content == "嗯" and bogus.reply_to is None, (bogus.content, bogus.reply_to)


async def test_dm_entry_strips_marker_and_keeps_the_intent(migrated_db):
    from sqlalchemy import text

    from app.chat.dm import get_or_create_dm_session, send_dm_message
    from app.database import async_session
    from db_reset import clear

    async with async_session() as db:
        await clear(db, "dm_messages", "dm_sessions", "users")
        await db.execute(text(
            "INSERT INTO users (id, username, password_hash, type) VALUES "
            "(1, '群主', 'x', 'human'), (40, '化学老师', 'x', 'ai')"
        ))
        await db.commit()
        created = await get_or_create_dm_session(db, 1, 40, skip_friendship_check=True)
        session_id = created["session_id"]
        first = await send_dm_message(
            db, session_id, sender_id=1, content="在吗", skip_friendship_check=True
        )
        await db.commit()

        reply = await send_dm_message(
            db, session_id, sender_id=40, content=f"在的 [msg_id={first['id']}]",
            skip_friendship_check=True,
        )
        await db.commit()
        assert reply["content"] == "在的", reply["content"]
        assert reply["reply_to"] == first["id"], reply
