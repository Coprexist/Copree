"""空焦段告知：一次性事实落历史，不在每轮摘要里念

会话焦段里的会话全走了，锚在它上面的记忆在哪儿都召不回（见 docs/memory_system/design/
focus_and_memory_reach.md §九），而 AI 自己看不见这件事。它以前挂在状态摘要里（每轮重拼 =
每轮全价），现在与便签/能力变更通知同一出口落成账本条目；投递进度借会话帧的 delivered 记，
解锁随之归零——条件还在，下一段上下文重新提醒。
"""
import pytest
from sqlalchemy import text

pytestmark = pytest.mark.anyio


async def _seed(db):
    from db_reset import clear

    await clear(db, "agents", "users")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES (1, 'u', 'x', 'human')"))
    await db.execute(text(
        "INSERT INTO agents (id, owner_id, name, user_id, discoverable, state_stack) "
        "VALUES (1, 1, '测试AI', NULL, true, '[]'::jsonb)"))
    await db.commit()


async def _empty_focus(db):
    """建一个会话焦段、把 group:66 放进去、再让它消失——得到"空焦段"这个事实。"""
    from app.services.agent import focus_service

    _, focus, _ = await focus_service.create(db, 1, "学生群", "session")
    await focus_service.join_session(db, 1, focus["id"], "group:66")
    await focus_service.forget_session(db, 1, "group:66")
    await db.commit()
    return focus["id"]


async def test_empty_focus_is_not_announced_every_turn():
    """每轮重拼的状态摘要只复述归属，不再念空焦段（那是账本条目的事）"""
    from app.utils.pure.focus import SESSION, add, describe, join, leave

    foci, group, _ = add([], "学生群", SESSION)
    foci, _ = join(foci, group["id"], "group:64")
    foci, _ = leave(foci, group["id"], "group:64")

    line = describe(foci, "group:99", "")
    assert "空焦段" not in line and "merge_focus" not in line, line


async def test_empty_notice_lands_in_history_once(migrated_db):
    from app.ai.llm import _deliver_focus_notices
    from app.database import async_session
    from app.models.agent import Agent
    from app.services.agent.state_stack_service import ensure_active_frame

    async with async_session() as db:
        await _seed(db)
        fid = await _empty_focus(db)
        await ensure_active_frame(db, 1, "group_chat", "group:67", "群67", "某人")
        await db.commit()
        agent = await db.get(Agent, 1)

        out = await _deliver_focus_notices(db, agent)
        assert len(out) == 1 and "空焦段" in out[0]["content"], out
        assert "学生群" in out[0]["content"] and fid in out[0]["content"]
        assert "merge_focus" in out[0]["content"], "只报警不给出路等于让 AI 干瞪眼"

        # 说过就不再占 token（投递进度记在这个会话帧上）
        assert await _deliver_focus_notices(db, agent) == []


async def test_unlock_makes_it_speak_again_until_the_focus_is_not_empty(migrated_db):
    """解锁 = 换了一段上下文：事实还在就重新说一遍；焦段重新有人了就闭嘴"""
    from app.ai.llm import _deliver_focus_notices
    from app.database import async_session
    from app.models.agent import Agent
    from app.services.agent import focus_service
    from app.services.agent.state_stack_service import ensure_active_frame, reset_frame_trigger_state

    async with async_session() as db:
        await _seed(db)
        fid = await _empty_focus(db)
        await ensure_active_frame(db, 1, "group_chat", "group:67", "群67", "某人")
        await db.commit()
        agent = await db.get(Agent, 1)

        assert len(await _deliver_focus_notices(db, agent)) == 1
        await reset_frame_trigger_state(db, 1)
        await db.commit()
        assert len(await _deliver_focus_notices(db, agent)) == 1, "解锁后条件还在，要重新提醒"

        await focus_service.join_session(db, 1, fid, "group:68")
        await reset_frame_trigger_state(db, 1)
        await db.commit()
        assert await _deliver_focus_notices(db, agent) == [], "焦段重新有人了，这条事实不再成立"


async def test_builtin_all_chats_is_never_an_empty_focus():
    """预置「所有聊天」本来就没有元素，不该被当成空焦段念"""
    from app.utils.pure.focus import ALL_CHATS_ID, empty_pending, empty_sessions, normalize

    foci = normalize([])
    assert [f["id"] for f in foci] == [ALL_CHATS_ID]
    assert empty_sessions(foci) == [] and empty_pending(foci, {}) == []
