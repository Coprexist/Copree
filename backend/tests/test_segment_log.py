"""状态段收尾：离开哪一帧，就把那一段落进它自己的桶

原先一条日志只对应一次模型调用，而请求体在开轮时定型——一轮中途 enter_state / pop_state
换掉的段落在日志里一点痕迹都没有。这里覆盖补上的那一条：桶按**被离开的帧**算，内容取那一刻
手上那份请求体（状态块换成这一段的样子），并且它不是一次模型调用。
"""
import pytest
from sqlalchemy import select, text

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


async def _enter_group(db):
    from app.services.agent.state_stack_service import ensure_active_frame

    await ensure_active_frame(db, 1, "group_chat", "group:66", "诗", "在群里说话")
    await db.commit()


async def _park_browse(db) -> str:
    """插件预建的沉睡帧：进它之前不改变当前帧"""
    from app.services.agent.state_stack_service import park_frame
    from app.utils.pure.state_stack import make_state_frame

    frame = make_state_frame(type_="bilibili_browse", context_ref="bilibili:1",
                             label="刷 B 站", doing="看一条视频")
    await park_frame(db, 1, frame)
    await db.commit()
    return str(frame["id"])


def _request_body(stack) -> list[dict]:
    """开轮时拼的那份请求体（状态块 = 开轮时在哪帧）"""
    from app.utils.pure.state_stack import format_state_stack_summary

    return [
        {"role": "system", "content": "你是测试 AI。"},
        {"role": "user", "content": "在吗"},
        {"role": "system", "content": format_state_stack_summary(stack)},
        {"role": "system", "content": "## 当前时间\n2026-11-01 18:00"},
    ]


async def _log(db, log_id):
    from app.models.conversation_log import ConversationLog

    return await db.get(ConversationLog, int(log_id))


async def test_leaving_a_frame_files_the_segment_under_that_frame(migrated_db):
    """群帧 → 浏览帧：多出来的一条归群聊桶，内容是离开群帧那一刻的样子"""
    from app.database import async_session
    from app.services.agent.segment_log import record_leave
    from app.services.agent.state_stack_service import get_frames, pull_frame
    from app.utils.pure.state_stack import state_frame_of

    async with async_session() as db:
        await _seed(db)
        await _enter_group(db)
        browse = await _park_browse(db)
        agent = await _agent(db)
        stack_before = await get_frames(db, 1)
        body = _request_body(stack_before)

        await pull_frame(db, 1, browse, leave="suspend")
        await db.commit()
        row = await _log(db, await record_leave(db, agent, stack_before=stack_before,
                                                messages=body, conversation_type="group"))
        await db.commit()

        assert row is not None
        assert row.origin == "switch"
        assert row.state_key == "group_chat|群「诗」"
        # 不是模型调用：没有用量、没有产出
        assert row.token_usage is None
        assert row.has_output is False
        # 落的是这一段当时的样子，末尾一句交代去向
        assert state_frame_of(row.messages) == {"type": "group_chat", "label": "群「诗」"}
        assert "在群里说话" in row.messages[2]["content"]
        assert "进入" in row.messages[-1]["content"]
        assert row.messages[0]["content"] == "你是测试 AI。"


async def test_popping_back_files_the_browse_segment_under_browse(migrated_db):
    """浏览帧 → 群帧：状态块要换成浏览帧那块，桶也跟着变成浏览桶"""
    from app.database import async_session
    from app.services.agent.segment_log import record_leave
    from app.services.agent.state_stack_service import get_frames, pop_state, pull_frame
    from app.utils.pure.state_stack import state_frame_of

    async with async_session() as db:
        await _seed(db)
        await _enter_group(db)
        browse = await _park_browse(db)
        agent = await _agent(db)
        group_body = _request_body(await get_frames(db, 1))

        await pull_frame(db, 1, browse, leave="suspend")
        await db.commit()
        stack_before = await get_frames(db, 1)

        await pop_state(db, 1)
        await db.commit()
        row = await _log(db, await record_leave(db, agent, stack_before=stack_before,
                                                messages=group_body, conversation_type="group"))
        await db.commit()

        assert row.state_key == "bilibili_browse|刷 B 站"
        assert state_frame_of(row.messages) == {"type": "bilibili_browse", "label": "刷 B 站"}
        assert "看一条视频" in row.messages[2]["content"]
        assert "[group_chat]" in row.messages[-1]["content"]


async def test_no_switch_logs_nothing(migrated_db):
    """只改了帧里的内容、或工具失败：当前帧没换，就不该凭空多一条"""
    from app.database import async_session
    from app.services.agent.segment_log import record_leave
    from app.services.agent.state_stack_service import get_frames

    async with async_session() as db:
        await _seed(db)
        await _enter_group(db)
        agent = await _agent(db)
        stack_before = await get_frames(db, 1)
        body = _request_body(stack_before)

        assert await record_leave(db, agent, stack_before=stack_before,
                                  messages=body, conversation_type="group") is None


async def test_switch_rows_have_no_outcome(migrated_db):
    """列表上的结局只属于模型调用；切换行按来源标记，别混成「正常」或「无输出」"""
    from app.database import async_session
    from app.services.agent.segment_log import record_leave
    from app.services.agent.state_stack_service import get_frames, pull_frame
    from app.services.content.conversation_log_service import _log_to_summary

    async with async_session() as db:
        await _seed(db)
        await _enter_group(db)
        browse = await _park_browse(db)
        agent = await _agent(db)
        stack_before = await get_frames(db, 1)
        body = _request_body(stack_before)

        await pull_frame(db, 1, browse, leave="suspend")
        await db.commit()
        row = await _log(db, await record_leave(db, agent, stack_before=stack_before,
                                                messages=body, conversation_type="group"))
        await db.commit()

        summary = _log_to_summary(row)
        assert summary["origin"] == "switch"
        assert summary["status"] is None
        assert summary["state_frame"] == {"type": "group_chat", "label": "群「诗」"}


async def test_frame_without_label_buckets_by_its_context_ref(migrated_db):
    """push_state 只给 context_ref、没写 label：桶名以请求体那块摘要为准，不落成 world|"""
    from app.database import async_session
    from app.services.agent.segment_log import record_leave
    from app.services.agent.state_stack_service import (
        get_frames, park_frame, pop_state, pull_frame,
    )
    from app.utils.pure.state_stack import make_state_frame, state_frame_of

    async with async_session() as db:
        await _seed(db)
        await _enter_group(db)
        agent = await _agent(db)

        # 他手压的一帧：只有 context_ref，没有 label（线上 push_state 就这么写过一帧）
        frame = make_state_frame(type_="world", context_ref="bilibili:81", doing="刷一轮视频")
        await park_frame(db, 1, frame)
        await db.commit()
        await pull_frame(db, 1, str(frame["id"]), leave="suspend")
        await db.commit()

        stack_before = await get_frames(db, 1)
        body = _request_body(stack_before)
        assert stack_before[-1].get("label") in (None, "")

        await pop_state(db, 1)
        await db.commit()
        row = await _log(db, await record_leave(db, agent, stack_before=stack_before,
                                                messages=body, conversation_type="group"))
        await db.commit()

        assert row.state_key == "world|bilibili:81"
        assert state_frame_of(row.messages) == {"type": "world", "label": "bilibili:81"}
