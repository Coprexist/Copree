"""群消息投递契约：不在线也暂存（用户 2026-09-23）。

文档已经定了（docs/chat_service/design/chat_service_design.md §4.2 可达性矩阵、cpec.md
「状态为 dnd/offline 则暂存」）：在线且不 DND → 直推；@提及 连 DND 也穿透；
DND/暂停 → 暂存；**不在线 → 一律暂存**。

旧实现把整个投递循环关在 `if online_ids:` 里，只遍历在线连接 —— "离线也暂存"这一格是漏的，
于是 pending_messages 整表为空、AI 回来 view_unread 报「0 条未读、没有消息记录」。
"""
import inspect

from app.chat.delivery import delivery_decision


def test_online_and_free_pushes():
    assert delivery_decision(online=True, in_dnd=False, mentioned=False) == "push"


def test_offline_always_pends_even_when_mentioned():
    """不在线就攒着：@提及 的穿透力只在"在线"这条前提成立时才有意义。"""
    assert delivery_decision(online=False, in_dnd=False, mentioned=False) == "pending"
    assert delivery_decision(online=False, in_dnd=False, mentioned=True) == "pending"
    assert delivery_decision(online=False, in_dnd=True, mentioned=True) == "pending"


def test_dnd_pends_but_mention_pierces():
    assert delivery_decision(online=True, in_dnd=True, mentioned=False) == "pending"
    assert delivery_decision(online=True, in_dnd=True, mentioned=True) == "push"


def test_group_broadcast_uses_the_shared_decision():
    """投递循环只能有一份：口径走 delivery_decision，且不能把自己关在 `if online_ids:` 里
    （那正是漏掉离线的写法）。实现收在 app/chat/group_delivery.py——网页端与外部通道
    （QQ 通道）共用它，路由只负责调用，不许再内联回去。"""
    from app.chat import group_delivery
    from app.routers import ws

    src = inspect.getsource(group_delivery)
    assert "delivery_decision(" in src
    assert "if online_ids:" not in src

    ws_src = inspect.getsource(ws)
    assert "fanout_group_message(" in ws_src      # 路由必须委派
    assert "store_pending_message(" not in ws_src  # 不许自己攒未读


async def test_group_entry_normalizes_mentions_to_ids(migrated_db):
    """入口归一：群里发的 @名字 落库就是 <@!id>（旧令牌不动、外人不动）。

    为什么在真库上验：这一步是"之后全链路只认 id"的前提——唤醒、未读、AI 上下文、
    通道出口都读库里的正文，入口漏一个写法，下游就得各自再比一遍名字。
    """
    from sqlalchemy import text

    from app.chat.gm import send_gm_message
    from app.database import async_session

    async with async_session() as db:
        from db_reset import clear
        await clear(db, "pending_messages", "messages", "group_members", "groups", "agents", "users")
        await db.execute(text(
            "INSERT INTO users (id, username, password_hash, type) VALUES "
            "(1, '群主', 'x', 'human'), (41, '浮生（人物志1）', 'x', 'ai'), (7, '小明', 'x', 'human')"
        ))
        await db.execute(text(
            "INSERT INTO groups (id, name, owner_type, owner_id, avatar_mode, include_ai_in_avatar) "
            "VALUES (59, 'CoExisten', 'human', 1, 'default', true)"
        ))
        await db.execute(text(
            "INSERT INTO group_members (group_id, member_type, member_id, role) VALUES "
            "(59, 'ai', 41, 'member'), (59, 'human', 1, 'owner'), (59, 'human', 7, 'member')"
        ))
        await db.commit()

        message = await send_gm_message(
            db, group_id=59, sender_type="human", sender_id=1,
            content="@浮生（人物志1） 你看 <@!7> 这条，@路人甲 就别管了",
        )
        await db.commit()
        assert message.content == "<@!41> 你看 <@!7> 这条，@路人甲 就别管了", message.content


async def test_stored_message_shows_up_in_unread_summary(migrated_db):
    """真库闭环：给离线的 AI 暂存一条群消息 → check_unread 按群读得到它。"""
    from sqlalchemy import text

    from app.chat.delivery import check_unread, store_pending_message
    from app.database import async_session

    async with async_session() as db:
        from db_reset import clear
        await clear(db, "pending_messages", "messages", "group_members", "groups", "agents", "users")
        await db.execute(text(
            "INSERT INTO users (id, username, password_hash, type) VALUES "
            "(1, '群主', 'x', 'human'), (41, '浮生', 'x', 'ai')"
        ))
        await db.execute(text(
            "INSERT INTO agents (id, owner_id, name, user_id, discoverable) VALUES "
            "(25, 1, '浮生（人物志1）', 41, true)"
        ))
        await db.execute(text(
            "INSERT INTO groups (id, name, owner_type, owner_id, avatar_mode, include_ai_in_avatar) "
            "VALUES (59, 'CoExisten', 'human', 1, 'default', true)"
        ))
        await db.execute(text(
            "INSERT INTO group_members (group_id, member_type, member_id, role) VALUES "
            "(59, 'ai', 41, 'member'), (59, 'human', 1, 'owner')"
        ))
        await db.execute(text(
            "INSERT INTO messages (id, group_id, sender_type, sender_id, content) VALUES "
            "(1, 59, 'human', 1, '@浮生（人物志1） 你好')"
        ))
        await db.commit()

    # AI 不在线 → 投递判定是 pending → 写暂存（这一步以前整个缺失）
    assert delivery_decision(online=False, in_dnd=False, mentioned=True) == "pending"
    async with async_session() as db:
        await store_pending_message(db, agent_id=25, group_id=59, message_id=1)
        await db.commit()

    async with async_session() as db:
        unread = await check_unread(db, 25)
        assert len(unread) == 1, unread
        assert unread[0]["group_id"] == 59
        assert unread[0]["unread_count"] == 1
        assert unread[0]["last_message_at"] is not None
        assert "@浮生" in (unread[0]["last_message_preview"] or "")
