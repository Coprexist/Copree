"""set_alarm 的活跃闹钟上限（2026-09-26）

真机发现（核查化学老师未设闹钟时顺带查出）：上限校验始终未生效——
检查逻辑导入的 app.models.agent_alarm 不存在（异常被 except 静默捕获），
且计数条件为 status == "active"，而该列取值只有 pending / fired / cancelled。
"""
import pytest

pytestmark = pytest.mark.anyio


async def _seed(db, *, max_alarms: int = 2):
    from sqlalchemy import text

    from db_reset import clear

    await clear(db, "agent_alarms", "agent_triggers", "group_members", "groups", "agents", "users")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES "
        "(1, '群主', 'x', 'human'), (2, 'AI账号', 'x', 'ai')"
    ))
    await db.execute(text(
        "INSERT INTO agents (id, owner_id, name, user_id, discoverable, max_alarms) "
        "VALUES (24, 1, '化学老师', 2, true, :m)"
    ), {"m": max_alarms})
    await db.commit()


async def _add_alarm(db, status: str, count: int = 1):
    from sqlalchemy import text

    for _ in range(count):
        await db.execute(text(
            "INSERT INTO agent_alarms (agent_id, wake_at, task, status) VALUES "
            "(24, now() + interval '1 day', '旧闹钟', :s)"
        ), {"s": status})
    await db.commit()


async def test_alarm_limit_counts_pending_only(migrated_db):
    """上限按 pending 计数：达到 max_alarms 即拒绝；已触发（fired）的不占名额"""
    from app.database import async_session
    from app.tools.self_management.set_alarm import SetAlarm

    async with async_session() as db:
        await _seed(db, max_alarms=2)
        await _add_alarm(db, "fired")

        out = await SetAlarm().execute(db, 24, None, {"task": "提醒自己", "delay_seconds": 60}, {})
        assert out.get("success") is True, out
        assert out["wake_at"] and out["task"] == "提醒自己"

        await _add_alarm(db, "pending", 2)
        out = await SetAlarm().execute(db, 24, None, {"task": "再来一个", "delay_seconds": 60}, {})
        assert out.get("error") is True and "上限" in out["message"], out
