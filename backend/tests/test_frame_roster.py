"""帧清单：一次给全「有哪些帧」，之后只补增删

原先要进某帧得先 list_states 拿 id；不知道有哪帧时，AI 会自己 push_state 造一帧出来。
这里测的是替代方案：解锁点与首次建帧时给一份完整清单（进固定块，字节不动），
之后帧的增删只补账本条目（投一次、解锁点随快照重写一起离场）。
"""
import pytest
from sqlalchemy import select, text

from app.utils.pure.state_stack import make_state_frame, render_frame_roster

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


async def _agent(db):
    from app.models.agent import Agent

    return (await db.execute(select(Agent).where(Agent.id == 1))).scalars().first()


async def _enter(db, n: int):
    from app.services.agent.state_stack_service import ensure_active_frame

    await ensure_active_frame(db, 1, "group_chat", f"group:{n}", str(n), "某人")
    await db.commit()


async def _notices(db) -> list[str]:
    """只认帧清单的条目：账本里还有别的通知（交接、后事），别把它们算进来"""
    rows = (await db.execute(text(
        "SELECT content FROM agent_history_entries WHERE kind = 'notice' ORDER BY id"))).all()
    return [r[0] for r in rows if r[0].startswith("【状态帧】")]


def test_roster_lists_running_frames_with_ids():
    frames = [make_state_frame("group_chat", "group:66", label="群「诗」", doing="在群里说话"),
              make_state_frame("bilibili_browse", "bilibili:1", label="刷 B 站")]
    text_ = render_frame_roster(frames)
    assert text_.startswith("## 🗂 你手上的状态帧")
    assert "[group_chat] 群「诗」 id=" in text_
    assert "[bilibili_browse] 刷 B 站 id=" in text_


def test_roster_caps_the_rows_and_points_at_list_states():
    frames = [make_state_frame("group_chat", f"group:{i}", label=f"群{i}") for i in range(6)]
    text_ = render_frame_roster(frames)
    assert "…另有 2 帧" in text_
    assert text_.count(" id=") == 4


def test_roster_is_empty_without_frames():
    assert render_frame_roster([]) == ""


def test_roster_uses_context_ref_when_label_is_missing():
    frame = make_state_frame("world", "bilibili:81")
    assert "[world] bilibili:81" in render_frame_roster([frame])


def test_dormant_frame_is_listed_and_marked():
    """插件预建的沉睡帧（status=ended）也要出现：那是他唯一能进去的入口"""
    live = make_state_frame("group_chat", "group:66", label="群「诗」")
    parked = make_state_frame("bilibili_browse", "bilibili:1", label="刷 B 站")
    parked["status"] = "ended"
    parked["dormant"] = True

    got = render_frame_roster([live, parked])
    assert "[bilibili_browse] 刷 B 站（沉睡）" in got


async def test_parked_frame_shows_up_in_the_roster(migrated_db):
    from app.database import async_session
    from app.services.agent.state_stack_service import frame_roster, park_frame

    async with async_session() as db:
        await _seed(db)
        await _enter(db, 66)
        await park_frame(db, 1, make_state_frame("bilibili_browse", "bilibili:1",
                                                 label="刷 B 站", doing="等着刷"))
        await db.commit()
        agent = await _agent(db)

        roster = await frame_roster(db, agent)
        await db.commit()

        assert "[bilibili_browse] 刷 B 站（沉睡）" in roster


async def test_entering_a_parked_frame_clears_the_marker(migrated_db):
    from app.database import async_session
    from app.services.agent.state_stack_service import (
        frame_roster, get_frames, park_frame, pull_frame,
    )

    async with async_session() as db:
        await _seed(db)
        await _enter(db, 66)
        frame = make_state_frame("bilibili_browse", "bilibili:1", label="刷 B 站")
        await park_frame(db, 1, frame)
        await db.commit()

        await pull_frame(db, 1, str(frame["id"]), leave="suspend")
        await db.commit()
        agent = await _agent(db)

        current = [f for f in await get_frames(db, 1) if f.get("context_ref") == "bilibili:1"][0]
        assert not current.get("dormant"), "进了状态链就不是沉睡帧了"
        roster = await frame_roster(db, agent)
        await db.commit()
        assert "（沉睡）" not in roster


async def test_first_render_is_complete_and_silent(migrated_db):
    from app.database import async_session
    from app.services.agent.state_stack_service import frame_roster

    async with async_session() as db:
        await _seed(db)
        await _enter(db, 66)
        agent = await _agent(db)

        roster = await frame_roster(db, agent)
        await db.commit()

        assert "[group_chat] 群「66」" in roster
        # 首次给全量，不必再补"多了一个"的条目
        assert await _notices(db) == []


async def test_adding_a_frame_keeps_the_block_and_adds_one_entry(migrated_db):
    from app.database import async_session
    from app.services.agent.state_stack_service import frame_roster

    async with async_session() as db:
        await _seed(db)
        await _enter(db, 66)
        agent = await _agent(db)
        first = await frame_roster(db, agent)
        await db.commit()

        await _enter(db, 67)          # 换个会话：66 挂起、67 成当前帧
        await _enter(db, 66)          # 又回到 66：67 挂着，还在状态链里
        agent = await _agent(db)

        roster = await frame_roster(db, agent)
        await db.commit()

        # 清单是快照：回到 66 时发的还是当初那份，新帧靠后面的条目补
        assert roster == first
        notices = await _notices(db)
        assert len(notices) == 1
        assert "多了一个" in notices[0] and "群「67」" in notices[0]


async def test_the_same_change_is_only_told_once(migrated_db):
    from app.database import async_session
    from app.services.agent.state_stack_service import frame_roster

    async with async_session() as db:
        await _seed(db)
        await _enter(db, 66)
        agent = await _agent(db)
        await frame_roster(db, agent)
        await db.commit()

        await _enter(db, 67)
        await _enter(db, 66)
        agent = await _agent(db)
        await frame_roster(db, agent)
        await db.commit()
        await frame_roster(db, agent)
        await db.commit()

        assert len(await _notices(db)) == 1


async def test_a_new_segment_of_the_same_session_inherits_the_block(migrated_db):
    """同一段会话换了帧实例（pop 之后再被叫回来）：清单字节不动，只补差集"""
    from app.database import async_session
    from app.services.agent.state_stack_service import frame_roster

    async with async_session() as db:
        await _seed(db)
        await _enter(db, 66)
        agent = await _agent(db)
        first = await frame_roster(db, agent)
        await db.commit()

        await _enter(db, 67)          # 去别的会话
        agent = await _agent(db)
        await frame_roster(db, agent)  # 67 也有了自己的清单
        await db.commit()

        await _enter(db, 66)          # 回 66：这里会为新段建一帧（旧段已 ended）
        agent = await _agent(db)
        roster = await frame_roster(db, agent)
        await db.commit()

        # 清单按会话继承：回到 66 时发的还是当初那份，不该因为换了帧实例而改写
        assert roster == first


async def test_unlock_renders_a_fresh_roster(migrated_db):
    from app.database import async_session
    from app.services.agent.state_stack_service import frame_roster, reset_frame_roster

    async with async_session() as db:
        await _seed(db)
        await _enter(db, 66)
        agent = await _agent(db)
        await frame_roster(db, agent)
        await db.commit()

        await _enter(db, 67)
        await _enter(db, 66)
        agent = await _agent(db)

        assert await reset_frame_roster(db, 1) is True
        roster = await frame_roster(db, agent)
        await db.commit()

        # 解锁点之后按此刻的帧集合重算：新帧直接出现在清单里
        assert "群「67」" in roster
