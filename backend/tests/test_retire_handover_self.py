"""帧后事由谁办：retire_handover_self（跟着预设档位走）

关（chat/immersive，也是存量 AI 的起步值）= 平台代销：超容量的帧直接删，只留一条告知；
开（digital_life）= 挂起待交接：记录留着，他处置完调 finish_frame 销掉。
会话消失（群解散）走同一把开关，只是来由不同。
"""
import pytest
from sqlalchemy import text

pytestmark = pytest.mark.anyio


async def _seed(db, handover: bool):
    from db_reset import clear

    await clear(db, "agents", "users")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES (1, 'u', 'x', 'human')"))
    await db.execute(text(
        "INSERT INTO agents (id, owner_id, name, user_id, discoverable, state_stack, retire_handover_self) "
        f"VALUES (1, 1, '测试AI', NULL, true, '[]'::jsonb, {str(handover).lower()})"))
    await db.commit()


async def _open(db, n: int):
    from app.services.agent.state_stack_service import ensure_active_frame

    await ensure_active_frame(db, 1, "group_chat", f"group:{n}", f"群{n}", "某人")
    await db.commit()


async def test_switch_off_drops_the_overflow_and_leaves_a_notice(migrated_db):
    """关：帧位满了平台直接销（不留记录），但必须留一条「平台代销」告知"""
    from app.database import async_session
    from app.services.agent.state_stack_service import current, get_frames

    async with async_session() as db:
        await _seed(db, handover=False)
        await db.execute(text("UPDATE agents SET frame_capacity = 2 WHERE id = 1"))
        await db.commit()
        await _open(db, 1)
        await _open(db, 2)
        await _open(db, 3)

        frames = await get_frames(db, 1)
        refs = [f["context_ref"] for f in frames]
        assert refs == ["group:2", "group:3"], f"最久没调用的那帧被销掉，实际 {refs}"
        notes = current(frames).get("pending_notices") or []
        assert [n["reason"] for n in notes] == ["profile"], notes
        assert [f["id"] for f in notes[0]["frames"]], "告知要写清销了哪几帧"


async def test_switch_on_keeps_the_record_for_handover(migrated_db):
    """开：同样超容量，但记录留着等他交接——同一套挑选规则，只差留不留记录"""
    from app.database import async_session
    from app.services.agent.state_stack_service import current, get_frames

    async with async_session() as db:
        await _seed(db, handover=True)
        await db.execute(text("UPDATE agents SET frame_capacity = 2 WHERE id = 1"))
        await db.commit()
        await _open(db, 1)
        await _open(db, 2)
        await _open(db, 3)

        frames = await get_frames(db, 1)
        assert [f["context_ref"] for f in frames] == ["group:1", "group:2", "group:3"]
        assert next(f for f in frames if f["context_ref"] == "group:1")["status"] == "retired"
        assert not (current(frames).get("pending_notices") or []), "挂起不走代销，没有代销告知"


async def test_lru_is_by_last_call_not_creation(migrated_db):
    """'最久'按最后一次调用算：老会话刚被叫过就不该被挑走"""
    from app.database import async_session
    from app.services.agent.state_stack_service import bump_frame_call_count, get_frames

    async with async_session() as db:
        await _seed(db, handover=False)
        await db.execute(text("UPDATE agents SET frame_capacity = 2 WHERE id = 1"))
        await db.commit()
        await _open(db, 1)
        await _open(db, 2)
        # 回到 1 号会话（它成为当前帧），再切到 3：被挑走的应是 2
        await _open(db, 1)
        await _open(db, 3)

        refs = [f["context_ref"] for f in await get_frames(db, 1)]
        assert refs == ["group:1", "group:3"], refs


async def test_dispose_context_frames_retires_when_handover_on(migrated_db):
    """会话消失（群解散）：接手的档位把帧留下待交接（它再也跑不起来，但记录要留）"""
    from app.database import async_session
    from app.services.agent.state_stack_service import current, dispose_context_frames, get_frames

    async with async_session() as db:
        await _seed(db, handover=True)
        await _open(db, 66)
        await _open(db, 67)

        out = await dispose_context_frames(db, "group:66")
        await db.commit()
        assert out == {"retired": 1, "dropped": 0}, out
        frames = await get_frames(db, 1)
        gone = next(f for f in frames if f["context_ref"] == "group:66")
        assert gone["status"] == "retired" and gone["retired_at"]
        assert not (current(frames).get("pending_notices") or [])


async def test_dispose_context_frames_drops_and_notifies_when_off(migrated_db):
    """同一个会话消失，不接手的档位：帧删掉，剩下的帧上留一条「会话已不存在」的告知

    被销的正是当前帧时也一样——剩下的帧顶上来当告知的落点，绝不静默。
    """
    from app.database import async_session
    from app.services.agent.state_stack_service import current, dispose_context_frames, get_frames

    async with async_session() as db:
        await _seed(db, handover=False)
        await _open(db, 66)
        await _open(db, 67)

        out = await dispose_context_frames(db, "group:67")
        await db.commit()
        assert out == {"retired": 0, "dropped": 1}, out
        frames = await get_frames(db, 1)
        assert [f["context_ref"] for f in frames] == ["group:66"], frames
        notes = current(frames).get("pending_notices") or []
        assert [n["reason"] for n in notes] == ["session"], notes
        assert [f["id"] for f in notes[0]["frames"]], "要写清销了哪几帧"


async def test_drop_notice_says_which_reason(migrated_db):
    """三种来由的告知要能分清：积压 / 这档不接手 / 会话没了"""
    from app.utils.pure.handover import drop_note, format_drop_notice

    frames = [{"id": "f1", "type": "group_chat", "label": "群1"}]
    backlog = format_drop_notice(drop_note(frames, "backlog"))
    profile = format_drop_notice(drop_note(frames, "profile"))
    session = format_drop_notice(drop_note(frames, "session"))
    assert "积压" in backlog and "retire_handover_self" not in backlog
    assert "retire_handover_self" in profile, "不接手那一档要给出开开关的出路"
    assert "会话" in session and "私有记忆" in session, "会话没了要说清记忆怎么了"
    assert len({backlog, profile, session}) == 3
