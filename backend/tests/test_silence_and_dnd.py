"""按人静音（静音三档里针对个人的那条，2026-09-26）

1. 只对某个人：哪怕他 @ 你也不唤醒，而且是**单向**的——他照常在群里说话、别人照常收到；
2. 按"多少分钟内 / 他再说多少条内"，条数按他说的每条扣，扣完自动恢复；
3. 与免打扰、屏蔽的分工见 docs/chat_service/design/chat_service_design.md §4.2/§4.3。

判定在 app/ai/decider.py 的 Gate 0（按人静音）与 Gate 2a/2b（屏蔽 / 免打扰穿透），
状态在 member_silences / group_members.muted_until，存取在 app/chat/delivery.py。
"""
import pytest

pytestmark = pytest.mark.anyio


async def _seed(db):
    from sqlalchemy import text

    from db_reset import clear

    await clear(db, "member_silences", "group_members", "groups", "agents", "users")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES "
        "(1, '群主', 'x', 'human'), (2, '测试AI账号', 'x', 'ai'), (90, '爱说话的人', 'x', 'human')"
    ))
    await db.execute(text(
        "INSERT INTO agents (id, owner_id, name, user_id, discoverable) "
        "VALUES (24, 1, '化学老师', 2, true)"
    ))
    await db.execute(text(
        "INSERT INTO groups (id, name, owner_type, owner_id, avatar_mode, include_ai_in_avatar) "
        "VALUES (64, '群', 'human', 1, 'default', true)"
    ))
    await db.execute(text(
        "INSERT INTO group_members (group_id, member_type, member_id, role) VALUES "
        "(64, 'human', 1, 'owner'), (64, 'ai', 2, 'member'), (64, 'human', 90, 'member')"
    ))
    await db.commit()


async def _agent(db):
    from sqlalchemy import select

    from app.models.agent import Agent

    return (await db.execute(select(Agent).where(Agent.id == 24))).scalar_one()


async def test_silenced_person_cannot_wake_the_ai_even_with_mention(migrated_db):
    """按人静音连 @ 都不唤醒；条数按"他说的每条"扣，扣完这条静音就失效"""
    from app.ai.decider import ActionContext, decide_action
    from app.chat.delivery import get_active_silence, silence_member
    from app.database import async_session

    async with async_session() as db:
        await _seed(db)
        agent = await _agent(db)
        await silence_member(db, 24, 64, 90, message_count=2)
        await db.commit()

        ctx = ActionContext(event_type="message", agent_id=24, group_id=64,
                            content="<@!2> 在吗", sender_type="human", sender_id=90,
                            is_mentioned=True)
        first = await decide_action(db, agent, ctx)
        assert first.should_act is False, first
        assert "静音" in first.reason, first.reason
        assert first.details.get("silenced_user_id") == 90, first.details
        row = await get_active_silence(db, 24, 64, 90)
        assert row is not None and row.remaining_count == 1, "扣了一次，还剩 1 条"

        second = await decide_action(db, agent, ctx)
        assert second.should_act is False and "静音" in second.reason, second
        assert await get_active_silence(db, 24, 64, 90) is None, "条数扣完 → 静音失效"


async def test_silence_expires_by_time(migrated_db):
    """时间到点就失效：过期的静音不该再拦人"""
    from datetime import datetime, timedelta

    from app.chat.delivery import get_active_silence
    from app.database import async_session
    from app.models.group import MemberSilence

    async with async_session() as db:
        await _seed(db)
        db.add(MemberSilence(agent_id=24, group_id=64, target_user_id=90,
                             until_at=datetime.utcnow() - timedelta(minutes=1)))
        await db.commit()
        assert await get_active_silence(db, 24, 64, 90) is None


async def test_silence_set_and_cancel_roundtrip(migrated_db):
    """时长与条数各自可空（都空 = 永久）；取消是幂等的"""
    from app.chat.delivery import (
        cancel_member_silence, consume_silence, get_active_silence, silence_member,
    )
    from app.database import async_session

    async with async_session() as db:
        await _seed(db)
        row = await silence_member(db, 24, 64, 90, duration_minutes=10, message_count=5)
        await db.commit()
        assert row.until_at is not None and row.remaining_count == 5, row

        await consume_silence(db, await get_active_silence(db, 24, 64, 90))
        await db.commit()
        assert (await get_active_silence(db, 24, 64, 90)).remaining_count == 4

        assert await cancel_member_silence(db, 24, 64, 90) is True
        await db.commit()
        assert await get_active_silence(db, 24, 64, 90) is None
        assert await cancel_member_silence(db, 24, 64, 90) is False, "再取消一次 = 幂等"


async def test_silence_tool_is_registered(migrated_db):
    """工具得让 AI 看得见：描述里必须讲清"连 @ 也不唤醒"和两种维度"""
    from app.tools.base import ToolRegistry

    names = [d["function"]["name"] for d in ToolRegistry.get_all_definitions()]
    assert "silence_member" in names, "按人静音工具没注册"

    spec = next(d["function"] for d in ToolRegistry.get_all_definitions()
                if d["function"]["name"] == "silence_member")
    assert "@" in spec["description"] and "message_count" in spec["parameters"]["properties"]


async def test_silence_tool_says_it_is_one_way(migrated_db):
    """工具返回值是 AI 当场唯一的解释：须自述「单向、无人被禁言」，并向账本自报一句摘要
    （账本仅记工具名时，后续轮次易误判为「对方已被禁言」）"""
    from app.database import async_session
    from app.tools.base import ToolRegistry
    from app.tools.chat_social.silence_member import SilenceMember

    async with async_session() as db:
        await _seed(db)
        out = await SilenceMember().execute(
            db, 24, 64, {"target_user_id": 90, "duration_minutes": 30}, {})

    assert out["success"] is True, out
    assert "只对你自己" in out["message"] and "没人被禁言" in out["message"], out["message"]
    assert out["__note"] == "只对 90 单向（30 分钟）", out["__note"]

    spec = next(d["function"] for d in ToolRegistry.get_all_definitions()
                if d["function"]["name"] == "silence_member")
    assert "单向" in spec["description"] and "禁言" in spec["description"], spec["description"]
