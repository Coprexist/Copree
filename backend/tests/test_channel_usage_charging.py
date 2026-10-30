"""通道对话额度记账：save_conversation_log 是唯一入口，调用方传会话还是仓库都得记上

2026-10-30 线上：executor 那条最热的路传的是 AsyncSession，而额度那段要用仓库的 `.session`
包 SAVEPOINT，于是每一轮都打「累加通道对话用量失败 (agent=1): 'AsyncSession' object has no
attribute 'session'」，账一直停在 0——错误被 try/except 咽成一行日志，没人发现。
"""
from sqlalchemy import text

USAGE = {"total_tokens": 120, "prompt_tokens": 100, "cached_tokens": 80,
         "completion_tokens": 20, "api_calls": 1}


async def _seed(db):
    from db_reset import clear

    await clear(db, "channel_conversation_usage", "channel_token_policy", "ai_conversation_logs",
                "usage_daily", "groups", "agents", "users")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES "
        "(1, '主人', 'x', 'human'), (2, 'AI账号', 'x', 'ai')"))
    await db.execute(text(
        "INSERT INTO agents (id, owner_id, name, user_id, discoverable) "
        "VALUES (24, 1, '值班员', 2, true)"))
    await db.execute(text(
        "INSERT INTO groups (id, name, owner_type, owner_id, avatar_mode, include_ai_in_avatar, origin_channel) "
        "VALUES (73, 'QQ 群', 'human', 1, 'default', true, 'qq')"))
    await db.commit()


async def test_charge_works_when_the_caller_hands_a_raw_session(migrated_db):
    """executor 传的就是 AsyncSession：通道额度必须照样记上（传裸会话时这里曾经一直是空的）"""
    from app.database import async_session
    from app.services.content.conversation_log_service import save_conversation_log

    async with async_session() as db:
        await _seed(db)
        log_id = await save_conversation_log(
            db, 24, [{"role": "user", "content": "签到"}], conversation_type="group", group_id=73,
            token_usage=USAGE, has_output=True, model="deepseek-flash",
        )
        assert log_id, "对话日志要落下来"
        row = (await db.execute(text(
            "SELECT used_total, used_billable FROM channel_conversation_usage "
            "WHERE agent_id=24 AND conversation='group:73'"))).mappings().first()
        assert row is not None, "通道对话必须记上账"
        assert (row["used_total"], row["used_billable"]) == (120, 40), dict(row)


async def test_charge_is_the_same_with_a_repository(migrated_db):
    """仓库写法口径一致：一条账只有一个口径，两种入参不能算出不同的数"""
    from app.database import async_session
    from app.repositories.content_repo import SQLAlchemyContentRepository
    from app.services.content.conversation_log_service import save_conversation_log

    async with async_session() as db:
        await _seed(db)
        log_id = await save_conversation_log(
            SQLAlchemyContentRepository(db), 24, [{"role": "user", "content": "签到"}],
            conversation_type="group", group_id=73, token_usage=USAGE,
            has_output=True, model="deepseek-flash",
        )
        assert log_id
        row = (await db.execute(text(
            "SELECT used_total, used_billable FROM channel_conversation_usage "
            "WHERE agent_id=24 AND conversation='group:73'"))).mappings().first()
        assert (row["used_total"], row["used_billable"]) == (120, 40), dict(row)
