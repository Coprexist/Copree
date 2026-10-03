"""记忆按账本投递：进过一次就只补改动量

- 第一次投全文；改过只补「以它为准」；指纹相同什么都不投；
- 判据是内容指纹（标题 + 正文 + 锚点），不是时间戳——权重衰减、被想起刷新时间都会写记忆行；
- 指纹只活在账本条目的 ref 里（服务端字段），请求体 content 里不许出现 id / 指纹 / 相似度。
"""
import pytest

pytestmark = pytest.mark.anyio


async def _seed(db):
    from sqlalchemy import text

    from db_reset import clear

    await clear(db, "agent_history_entries", "detail_memories", "rough_memories", "agents", "users")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES "
        "(1, '甲', 'x', 'human'), (2, 'AI账号', 'x', 'ai')"))
    await db.execute(text(
        "INSERT INTO agents (id, owner_id, name, user_id, discoverable) "
        "VALUES (24, 1, '化学老师', 2, true)"))
    await db.execute(text(
        "INSERT INTO rough_memories (id, owner_type, owner_id, scope, title, status) "
        "VALUES (7, 'ai', 24, 'private', '化学课代表', 'active')"))
    await db.execute(text(
        "INSERT INTO detail_memories (id, rough_id, content) VALUES (1, 7, '喜欢用类比讲题')"))
    await db.commit()


async def test_first_delivery_is_full_text_then_change_only(migrated_db):
    from sqlalchemy import text

    from app.database import async_session
    from app.models.agent import Agent
    from app.services.memory.memory_delivery import deliver_memories

    async with async_session() as db:
        await _seed(db)
        agent = await db.get(Agent, 24)

        first = await deliver_memories(db, agent, "group:1", [7])
        assert len(first) == 1, first
        assert "【想起】" in first[0]["content"] and "喜欢用类比讲题" in first[0]["content"]
        assert first[0]["ref"].startswith("mem:7@")
        assert "mem:7@" not in first[0]["content"], "指纹是服务端字段，不能进请求体"
        assert "相似度" not in first[0]["content"]

        # 同一版再投：一条都不加（没变化就不注）
        assert await deliver_memories(db, agent, "group:1", [7]) == []

        # 改正文 → 补一条「以它为准」，不重投全文
        await db.execute(text(
            "UPDATE detail_memories SET content = '喜欢用生活例子讲题' WHERE rough_id = 7"))
        await db.commit()
        changed = await deliver_memories(db, agent, "group:1", [7])
        assert len(changed) == 1 and "【记忆更新】" in changed[0]["content"], changed
        assert "喜欢用生活例子讲题" in changed[0]["content"]
        assert changed[0]["ref"] != first[0]["ref"]

        # 再改一次：仍然只补最新那一版，不补中间那版
        await db.execute(text(
            "UPDATE detail_memories SET content = '改用化学实验讲题' WHERE rough_id = 7"))
        await db.commit()
        again = await deliver_memories(db, agent, "group:1", [7])
        assert len(again) == 1 and "改用化学实验讲题" in again[0]["content"], again


async def test_change_reaches_context_that_no_longer_recalls_it(migrated_db):
    """召不回到也要更正：它已经在那个上下文里了，错了就得告诉它。"""
    from sqlalchemy import text

    from app.database import async_session
    from app.models.agent import Agent
    from app.services.memory.memory_delivery import deliver_memories

    async with async_session() as db:
        await _seed(db)
        agent = await db.get(Agent, 24)
        await deliver_memories(db, agent, "group:1", [7])

        await db.execute(text("UPDATE rough_memories SET title = '化学课代表（改）' WHERE id = 7"))
        await db.commit()

        events = await deliver_memories(db, agent, "group:1", [])   # 本轮没召回到它
        assert len(events) == 1 and "【记忆更新】" in events[0]["content"], events


async def test_fingerprint_ignores_weight_and_touch_time(migrated_db):
    """权重与"被想起"的时间不是「这条记忆说了什么」，改它们不该打扰 AI。"""
    from sqlalchemy import text

    from app.database import async_session
    from app.models.agent import Agent
    from app.services.memory.memory_delivery import deliver_memories

    async with async_session() as db:
        await _seed(db)
        agent = await db.get(Agent, 24)
        await deliver_memories(db, agent, "group:1", [7])

        await db.execute(text(
            "UPDATE rough_memories SET value_score = 1, last_touched_at = now(), "
            "last_touched_call = 99 WHERE id = 7"))
        await db.commit()
        assert await deliver_memories(db, agent, "group:1", [7]) == []
