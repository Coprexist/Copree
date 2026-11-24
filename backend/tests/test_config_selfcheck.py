"""配置卫生自检（#5）：造脏数据 → 断言问题列表，不靠读启动日志。

检查项见 docs/dev/api_config_chain.md §7。这里只测静态检查的"说得出来"，
不测 bootstrap 的调度（那是一个 spawn_task，失败也不影响启动）。
"""
import pytest
from sqlalchemy import text

pytestmark = pytest.mark.anyio


async def _clean(db):
    from db_reset import clear

    await clear(db, "user_api_assignments", "user_api_profiles",
                "user_api_providers", "api_key_pool", "agents", "users")


async def test_clean_database_reports_nothing(migrated_db):
    from app.database import async_session
    from app.services.agent.config_selfcheck import collect_api_config_problems

    async with async_session() as db:
        await _clean(db)
        await db.commit()
        assert await collect_api_config_problems(db) == []


async def test_dirty_configuration_is_reported_in_plain_words(migrated_db):
    from app.database import async_session
    from app.services.agent.config_selfcheck import collect_api_config_problems

    async with async_session() as db:
        await _clean(db)
        # 用户 1：裸列残留（迁移应已清空，说明有写入绕过配置档）
        await db.execute(text(
            "INSERT INTO users (id, username, password_hash, type, api_base_url) "
            "VALUES (7001, 'u1', 'x', 'human', 'https://leftover.example.com')"
        ))
        # 用户 2：两条默认档；用户 3：有档但没默认
        await db.execute(text(
            "INSERT INTO users (id, username, password_hash, type) VALUES "
            "(7002, 'u2', 'x', 'human'), (7003, 'u3', 'x', 'human')"
        ))
        await db.execute(text(
            "INSERT INTO user_api_providers (id, user_id, kind, name, base_url) VALUES "
            "(7010, 7002, 'llm', 'A', 'https://a.example.com'), "
            "(7011, 7002, 'llm', 'B', 'https://b.example.com'), "
            "(7012, 7003, 'llm', 'C', 'https://c.example.com'), "
            "(7013, 7003, 'voice', 'V', 'https://v.example.com')"
        ))
        await db.execute(text(
            "INSERT INTO user_api_profiles "
            "(id, user_id, provider_id, name, is_default) VALUES "
            "(7020, 7002, 7010, 'A', true), (7021, 7002, 7011, 'B', true), "
            "(7022, 7003, 7012, 'C', false), (7023, 7003, 7013, 'V', false)"
        ))
        # 一个 AI 被绑到语音档上 → 只有这种情况才算「厂商 kind 不符」
        await db.execute(text(
            "INSERT INTO agents (id, owner_id, name, llm_profile_id) VALUES (7007, 7003, '错绑', 7023)"
        ))
        await db.commit()

        problems = await collect_api_config_problems(db)

    joined = " | ".join(problems)
    assert "users #7001" in joined and "裸 Key" in joined
    assert "用户 #7002 有 2 条默认配置档" in joined
    assert "用户 #7003 有配置档但没有默认档" in joined
    assert "配置档 #7023" in joined and "不是 LLM" in joined
