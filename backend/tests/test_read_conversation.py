"""读别的会话的账本尾部（跨对话回复的"读"那一半）"""
import pytest
from sqlalchemy import text

pytestmark = pytest.mark.anyio


async def _seed(db):
    from db_reset import clear

    await clear(db, "agents", "users")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES "
        "(1, '测试用户', 'x', 'human'), (2, '测试AI账号', 'x', 'ai')"
    ))
    await db.execute(text(
        "INSERT INTO agents (id, owner_id, name, user_id, discoverable) "
        "VALUES (1, 1, '测试AI', 2, true)"
    ))
    await db.commit()


async def test_read_conversation_returns_that_conversations_tail(migrated_db):
    from app.database import async_session
    from app.services.history import history_service as hs
    from app.tools.chat_social.read_conversation import ReadConversation
    from app.utils.pure.history import make_entry

    async with async_session() as db:
        await _seed(db)
        await hs.append(db, 1, "group:64", [
            make_entry("message", "[64] 第一条", actor="user", ref="1"),
            make_entry("message", "[64] 第二条", actor="self", ref="2"),
            make_entry("message", "[64] 第三条", actor="user", ref="3"),
        ])
        await hs.append(db, 1, "group:77", [make_entry("message", "[77] 别的群", actor="user", ref="9")])
        await db.commit()

        r = await ReadConversation().execute(db, 1, None, {"group_id": 64, "limit": 2}, {})

        assert r["success"] is True and r["count"] == 2 and r["context_ref"] == "group:64"
        assert "[64] 第二条" in r["content"] and "[64] 第三条" in r["content"]
        assert "[64] 第一条" not in r["content"], "读的是尾部，不是最早那几条"
        assert "[77]" not in r["content"], "别的会话不能串进来"
        assert "别的会话" in r["content"], "必须说清这不是当前会话"

        missing = await ReadConversation().execute(db, 1, None, {}, {})
        assert missing["success"] is False, "不指定会话就报清楚，别猜"

        await hs.clear(db, 1, "group:64")
        await hs.clear(db, 1, "group:77")
        await db.commit()
