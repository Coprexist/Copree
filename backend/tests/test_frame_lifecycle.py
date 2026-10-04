"""状态帧的生命周期：全量保留 + 指针排队

存储 = agents.state_stack 整个数组（形状不变，只增不删）；
运行集合 = 数组里 status 处于 active/paused/suspended 的那串指针（"栈"只剩它）。
出运行集合有三种：pop/close（ended）、容量超限（retired）、会话解散（retired）。
记录只在**他交接完后事**（finish_frame）之后才删——那是唯一删除点。
"""
import pytest
from sqlalchemy import text

pytestmark = pytest.mark.anyio


async def _seed(db):
    from db_reset import clear

    await clear(db, "agents", "users")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES (1, 'u', 'x', 'human')"))
    # 这一份测的是"他自己接手后事"那一档（开关开）；开关关着的走法见 test_retire_handover_self.py
    await db.execute(text(
        "INSERT INTO agents (id, owner_id, name, user_id, discoverable, state_stack, retire_handover_self) "
        "VALUES (1, 1, '测试AI', NULL, true, '[]'::jsonb, true)"))
    await db.commit()


async def _open(db, n: int):
    """开第 n 个会话（每个会话一帧，前一帧挂起）。"""
    from app.services.agent.state_stack_service import ensure_active_frame

    await ensure_active_frame(db, 1, "group_chat", f"group:{n}", f"群{n}", "某人")
    await db.commit()


async def test_pop_keeps_the_record_and_shrinks_the_running_set(migrated_db):
    from app.database import async_session
    from app.services.agent.state_stack_service import get_frames, pop_state, running

    async with async_session() as db:
        await _seed(db)
        await _open(db, 1)
        await _open(db, 2)

        stack, _ = await pop_state(db, 1)
        await db.commit()
        frames = await get_frames(db, 1)

        assert len(frames) == 2, "记录不许丢：pop 只是让它出运行集合"
        assert [f["status"] for f in frames] == ["ended", "active"], frames
        assert frames[-1]["context_ref"] == "group:1", "回到上一层，当前帧仍在末尾"
        assert [f["context_ref"] for f in running(frames)] == ["group:1"]


async def test_close_keeps_the_record_too(migrated_db):
    from app.database import async_session
    from app.services.agent.state_stack_service import close_state, get_frames

    async with async_session() as db:
        await _seed(db)
        await _open(db, 1)

        await close_state(db, 1)
        await db.commit()
        frames = await get_frames(db, 1)
        assert [f["status"] for f in frames] == ["ended"], frames


async def test_overflow_retires_the_least_recently_active_frame(migrated_db):
    from app.database import async_session
    from app.services.agent.state_stack_service import get_frames

    async with async_session() as db:
        await _seed(db)
        await db.execute(text("UPDATE agents SET frame_capacity = 2 WHERE id = 1"))
        await db.commit()

        await _open(db, 1)
        await _open(db, 2)
        await _open(db, 3)          # 存储 3 帧 > 容量 2 → 挂起最久没激活的那个

        frames = await get_frames(db, 1)
        pend = [f for f in frames if f["status"] == "retired"]
        assert [f["context_ref"] for f in pend] == ["group:1"], frames
        assert pend[0].get("retired_at"), "挂起要留时间戳（谁被挂起过、什么时候）"
        assert len(frames) == 3, "挂起不删记录"


async def test_finish_frame_is_the_only_delete_point(migrated_db):
    from app.database import async_session
    from app.services.agent.state_stack_service import finish_frame, get_frames

    async with async_session() as db:
        await _seed(db)
        await db.execute(text("UPDATE agents SET frame_capacity = 2 WHERE id = 1"))
        await db.commit()
        await _open(db, 1)
        await _open(db, 2)
        await _open(db, 3)

        frames = await get_frames(db, 1)
        pend = next(f for f in frames if f["status"] == "retired")
        running_id = next(f["id"] for f in frames if f["status"] == "active")

        ok, msg = await finish_frame(db, 1, running_id)
        await db.commit()
        assert not ok and "还在运行" in msg, "跑着的状态要用 close_state 关，不能被交接单销掉"

        ok, _ = await finish_frame(db, 1, pend["id"])
        await db.commit()
        assert ok
        left = await get_frames(db, 1)
        assert pend["id"] not in [f["id"] for f in left] and len(left) == 2


async def test_finished_frame_can_be_revived_by_its_stable_id(migrated_db):
    """帧不再被随手删 → 闹钟记的帧指针长期有效：已结束的帧也能按 id 复活"""
    from app.database import async_session
    from app.services.agent.state_stack_service import (
        close_state, current, get_frames, restore_frame,
    )

    async with async_session() as db:
        await _seed(db)
        await _open(db, 1)
        frames = await get_frames(db, 1)
        fid = frames[0]["id"]

        await close_state(db, 1)
        await db.commit()

        revived = await restore_frame(db, 1, fid, "")
        await db.commit()
        assert revived["id"] == fid and revived["status"] == "active"
        assert current(await get_frames(db, 1))["id"] == fid

async def test_retired_backlog_has_a_hard_floor(migrated_db):
    """挂起也不能无限堆积：超过 capacity×2，平台代销最旧的，并在当前帧留一条待投递通知

    代销是唯一一处绕过他表态的删除，所以必须留痕——通知由构建提示词那一步落成历史条目。
    """
    from app.database import async_session
    from app.services.agent.state_stack_service import current, get_frames

    async with async_session() as db:
        await _seed(db)
        await db.execute(text("UPDATE agents SET frame_capacity = 1 WHERE id = 1"))
        await db.commit()

        for n in range(1, 5):
            await _open(db, n)          # 容量 1 → 硬底 2

        frames = await get_frames(db, 1)
        refs = [f["context_ref"] for f in frames]
        assert "group:1" not in refs, f"最旧的挂起帧应被平台代销，实际 {refs}"
        assert len([f for f in frames if f["status"] == "retired"]) == 2, frames

        notes = current(frames).get("pending_notices") or []
        assert notes and notes[0]["kind"] == "platform_drop", notes
        assert [d["id"] for d in notes[0]["frames"]], "代销要记下销了哪几帧"

async def _agent(db):
    from app.models.agent import Agent

    return await db.get(Agent, 1)


async def test_handover_notice_lands_in_history_once(migrated_db):
    """待交接清单落历史：一帧一条、按 ref 幂等（看过就算投过，不重复占 token）"""
    from app.ai.llm import _deliver_handover
    from app.database import async_session
    from app.services.agent.state_stack_service import get_frames
    from app.utils.pure.handover import handover_ref

    async with async_session() as db:
        await _seed(db)
        await db.execute(text("UPDATE agents SET frame_capacity = 1 WHERE id = 1"))
        await db.commit()
        await _open(db, 1)
        await _open(db, 2)

        pend = next(f for f in await get_frames(db, 1) if f["status"] == "retired")
        out = await _deliver_handover(db, await _agent(db), [])
        assert [e["ref"] for e in out] == [handover_ref(pend["id"])], out
        assert "finish_frame" in out[0]["content"], "要给出路，不能只报警"

        # 账本里已经有了 → 不再投
        assert await _deliver_handover(db, await _agent(db), out) == []


async def test_platform_drop_notice_is_delivered_and_marker_cleared(migrated_db):
    """平台代销必须留痕：投一条历史条目，并把当前帧上的标记清掉"""
    from app.ai.llm import _deliver_handover
    from app.database import async_session
    from app.services.agent.state_stack_service import current, get_frames
    from app.utils.pure.handover import DROP_PREFIX

    async with async_session() as db:
        await _seed(db)
        await db.execute(text("UPDATE agents SET frame_capacity = 1 WHERE id = 1"))
        await db.commit()
        for n in range(1, 5):
            await _open(db, n)

        out = await _deliver_handover(db, await _agent(db), [])
        drops = [e for e in out if e["ref"].startswith(DROP_PREFIX)]
        assert drops and "平台代销" in drops[0]["content"], out

        frames = await get_frames(db, 1)
        assert not (current(frames).get("pending_notices") or []), "投过就清标记"
        assert await _deliver_handover(db, await _agent(db), out) == []
