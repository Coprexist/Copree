"""AI 的显示名只有一个来源：agents.name（改名只写它，users.username 只是建号快照）。

私信列表与会话头曾经读 users.username，于是同一个 AI 在列表里叫旧名、在消息气泡里叫新名
（改名后列表一直不跟着动）。这里的用例把"人看得见的每一处名字都同口径"钉住。
"""
from sqlalchemy import text


async def _seed():
    from app.database import async_session

    async with async_session() as db:
        from db_reset import clear
        await clear(db, "dm_messages", "dm_sessions", "pending_messages", "messages",
                    "group_members", "groups", "agents", "users")
        await db.execute(text(
            "INSERT INTO users (id, username, password_hash, type) VALUES "
            "(1, '人类', 'x', 'human'), "
            "(41, '逍遥三号（人物志1）', 'x', 'ai'), "  # 建号时的名字：改名不会动 users 这一行
            "(42, '没有 agent 行的 AI', 'x', 'ai')"
        ))
        await db.execute(text(
            "INSERT INTO agents (id, owner_id, name, user_id, discoverable) VALUES "
            "(25, 1, '浮生（人物志1）', 41, true)"
        ))
        await db.execute(text(
            "INSERT INTO dm_sessions (session_id, user1_id, user2_id) VALUES ('1_41', 1, 41)"
        ))
        await db.execute(text(
            "INSERT INTO dm_messages (session_id, sender_id, content, message_type, created_at) VALUES "
            "('1_41', 1, '在吗', 'normal', '2026-09-28 09:00:00'), "
            "('1_41', 41, '在的', 'normal', '2026-09-28 09:01:00')"
        ))
        await db.commit()


async def test_dm_list_follows_agent_rename(migrated_db):
    """列表与气泡必须同口径：改名后列表也得是新名（曾经列表读 users.username 卡在旧名）。"""
    from app.chat.dm import list_dm_sessions
    from app.database import async_session

    await _seed()
    async with async_session() as db:
        rows = await list_dm_sessions(db, 1)
    by_session = {r["session_id"]: r for r in rows}
    assert by_session["1_41"]["partner"]["name"] == "浮生（人物志1）", by_session


async def test_dm_detail_partner_and_bubbles_agree(migrated_db):
    """会话头的 partner.name 与消息里的 sender_name 不能一个旧一个新。"""
    from app.chat.dm import get_dm_session
    from app.database import async_session

    await _seed()
    async with async_session() as db:
        detail = await get_dm_session(db, "1_41", 1, message_limit=10)
    assert detail["partner"]["name"] == "浮生（人物志1）"
    ai_names = {m["sender_name"] for m in detail["messages"] if m["sender_type"] == "ai"}
    assert ai_names == {"浮生（人物志1）"}, detail["messages"]


async def test_human_partner_keeps_username(migrated_db):
    """人类没有 agent 行，显示名就是 username（别把这条口径一起改坏）。"""
    from app.chat.dm import list_dm_sessions
    from app.database import async_session

    await _seed()
    async with async_session() as db:
        rows = await list_dm_sessions(db, 41)
    assert [r["partner"]["name"] for r in rows] == ["人类"]


async def test_ai_without_agent_row_falls_back_to_username(migrated_db):
    """没有 agent 行的 AI（世界助手这类）退回 username，查不到的行给「用户{id}」。"""
    from app.database import async_session
    from app.utils.display_name import display_names

    await _seed()
    async with async_session() as db:
        names = await display_names(db, [42, 41, 999999])
    assert names[42] == "没有 agent 行的 AI", names
    assert names[41] == "浮生（人物志1）", names
    assert names[999999] == "用户999999", names


async def test_mention_rendering_uses_display_name(migrated_db):
    """给人看的预览/导出把 <@!id> 换成名字——也得换上新名。"""
    from app.database import async_session
    from app.utils.message_serializer import mention_names
    from app.utils.text import render_mention_names

    await _seed()
    async with async_session() as db:
        names = await mention_names(db, ["你好 <@!41>"])
        assert names == {41: "浮生（人物志1）"}, names
    assert render_mention_names("你好 <@!41>", names) == "你好 @浮生（人物志1）"


async def test_group_mention_links_new_name_to_id(migrated_db):
    """入口把 @名字 归一成 <@!id>：人打的是界面上看见的名字，改名后 @新名 必须照样连得上。"""
    from app.chat.gm import send_gm_message
    from app.database import async_session

    await _seed()
    async with async_session() as db:
        await db.execute(text(
            "INSERT INTO groups (id, name, owner_type, owner_id, avatar_mode, include_ai_in_avatar) "
            "VALUES (59, 'CoExisten', 'human', 1, 'default', true)"
        ))
        await db.execute(text(
            "INSERT INTO group_members (group_id, member_type, member_id, role) VALUES "
            "(59, 'ai', 41, 'member'), (59, 'human', 1, 'owner')"
        ))
        await db.commit()
        message = await send_gm_message(
            db, group_id=59, sender_type="human", sender_id=1,
            content="@浮生（人物志1） 你好",
        )
        await db.commit()
    assert message.content == "<@!41> 你好", message.content
