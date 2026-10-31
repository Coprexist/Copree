"""search_messages：按关键词搜 AI 可见的聊天原文（只搜它在的会话）"""
from sqlalchemy import text

GROUP_IN, GROUP_OUT = 501, 502
AGENT_ID, ME = 901, 902


async def _seed(db):
    from db_reset import clear

    await clear(db, "messages", "dm_messages", "dm_sessions", "group_members", "groups",
                "agents", "users")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES "
        f"(1, '群主', 'x', 'human'), ({ME}, '本鲸', 'x', 'ai'), (910, '书爱', 'x', 'human')"
    ))
    await db.execute(text(
        "INSERT INTO agents (id, owner_id, name, user_id, discoverable) "
        f"VALUES ({AGENT_ID}, 1, '本鲸', {ME}, true)"
    ))
    await db.execute(text(
        f"INSERT INTO groups (id, name, owner_type, owner_id, avatar_mode, include_ai_in_avatar) "
        f"VALUES ({GROUP_IN}, '在的群', 'human', 1, 'default', true), "
        f"({GROUP_OUT}, '不在的群', 'human', 1, 'default', true)"
    ))
    await db.execute(text(
        f"INSERT INTO group_members (group_id, member_type, member_id, role) VALUES "
        f"({GROUP_IN}, 'ai', {ME}, 'member'), ({GROUP_IN}, 'human', 910, 'member'), "
        f"({GROUP_OUT}, 'human', 910, 'member')"
    ))
    await db.execute(text(
        f"INSERT INTO messages (group_id, sender_type, sender_id, content) VALUES "
        f"({GROUP_IN}, 'human', 910, '为什么你的形象和 D老师 吵起来了'), "
        f"({GROUP_IN}, 'human', 910, '今天天气不错'), "
        f"({GROUP_OUT}, 'human', 910, 'D老师 在别的群里')"
    ))
    await db.commit()


async def test_search_messages_only_searches_my_conversations(migrated_db):
    from app.database import async_session
    from app.tools.chat_social.search_messages import SearchMessages

    async with async_session() as db:
        await _seed(db)
        out = await SearchMessages().execute(db, AGENT_ID, GROUP_IN, {"query": "D老师"}, {})
        assert out["success"], out
        assert out["count"] == 1, out["results"]
        hit = out["results"][0]
        assert hit["conversation"] == f"group:{GROUP_IN}", hit
        assert "D老师" in hit["snippet"] and hit["message_id"] > 0, hit
        # 自己不在的群搜不到
        assert all(item["conversation"] != f"group:{GROUP_OUT}" for item in out["results"])


async def test_search_messages_supports_sender_and_days(migrated_db):
    from app.database import async_session
    from app.tools.chat_social.search_messages import SearchMessages

    async with async_session() as db:
        await _seed(db)
        out = await SearchMessages().execute(
            db, AGENT_ID, GROUP_IN, {"query": "天气", "sender_id": 910}, {})
        assert out["count"] == 1, out
        out = await SearchMessages().execute(
            db, AGENT_ID, GROUP_IN, {"query": "天气", "sender_id": 911}, {})
        assert out["count"] == 0, out
