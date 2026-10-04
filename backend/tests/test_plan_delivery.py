"""计划板按账本投递：同一版只投一次，变过才重投一份新板

- 键是**会话**（\`plan:<context_ref>\`），不是帧 id——帧会被 pop/重建，帧 id 当键会让板子每轮重投；
- 变没变比**渲染文本**（账本里最后一条 plan 条目的正文就是上次投出去的板子），没有指纹、没有版本列；
- 状态帧 id 是服务端字段，不进请求体；闹钟 #id 要显示（AI 靠它 update_alarm / cancel_alarm）。
"""
import pytest

pytestmark = pytest.mark.anyio

_FRAMES = ('[{"id": "frame-a", "type": "group_chat", "context_ref": "group:1", '
           '"label": "群「诗」", "doing": "在群里回复", "status": "active"}]')


async def _seed(db, *, enabled: bool = True):
    from sqlalchemy import text

    from db_reset import clear

    await clear(db, "agent_history_entries", "agent_alarms", "agents", "users")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES "
        "(1, '甲', 'x', 'human'), (2, 'AI账号', 'x', 'ai')"))
    await db.execute(text(
        "INSERT INTO agents (id, owner_id, name, user_id, discoverable, state_stack, "
        "plan_injection_enabled) VALUES (24, 1, '涵吾珑', 2, true, :stack, :on)"),
        {"stack": _FRAMES, "on": enabled})
    await db.commit()


async def _add_alarm(db, **over):
    from sqlalchemy import text

    row = {"id": 7, "wake_at": "now() + interval '1 hour'", "task": "整理本周聊天记录",
           "status": "pending", "frame_id": "frame-a", "origin": "group:1", "fired": "NULL"}
    row.update(over)
    await db.execute(text(
        "INSERT INTO agent_alarms (id, agent_id, wake_at, task, status, created_at, fired_at, "
        "frame_id, origin_context_ref) VALUES "
        f"(:id, 24, {row['wake_at']}, :task, :status, now(), {row['fired']}, "
        f":frame_id, :origin)"),
        {"id": row["id"], "task": row["task"], "status": row["status"],
         "frame_id": row["frame_id"], "origin": row["origin"]})
    await db.commit()


async def test_first_delivery_then_only_changes(migrated_db):
    from sqlalchemy import text

    from app.database import async_session
    from app.models.agent import Agent
    from app.services.agent.plan_service import deliver_plans

    async with async_session() as db:
        await _seed(db)
        agent = await db.get(Agent, 24)
        await _add_alarm(db)

        first = await deliver_plans(db, agent, "group:1")
        assert len(first) == 1, first
        body = first[0]["content"]
        assert first[0]["kind"] == "plan" and first[0]["ref"] == "plan:group:1"
        assert "【计划】" in body and "整理本周聊天记录" in body and "#7" in body
        assert "frame-a" not in body, "状态帧 id 是服务端字段，不能进请求体"

        # 同一版再投：一条都不加（没变就不注）
        assert await deliver_plans(db, agent, "group:1") == []

        # 改时间 → 重投一份新板（不是补差量：板子是一张快照）
        await db.execute(text("UPDATE agent_alarms SET wake_at = now() + interval '3 hours' WHERE id = 7"))
        await db.commit()
        changed = await deliver_plans(db, agent, "group:1")
        assert len(changed) == 1 and "【计划更新】" in changed[0]["content"], changed
        assert changed[0]["ref"] == "plan:group:1"

        # 再投一次：新板也稳了，不再重复
        assert await deliver_plans(db, agent, "group:1") == []

        # 取消 → 板子空了，得说一声，否则 AI 会以为旧计划还算数
        await db.execute(text("UPDATE agent_alarms SET status = 'cancelled' WHERE id = 7"))
        await db.commit()
        empty = await deliver_plans(db, agent, "group:1")
        assert len(empty) == 1 and "已经没有任何计划" in empty[0]["content"], empty
        assert await deliver_plans(db, agent, "group:1") == []


async def test_fired_plan_is_shown_then_board_settles(migrated_db):
    """已执行的在下一次重投时显示一行 ✅，然后板子重新稳定。"""
    from app.database import async_session
    from app.models.agent import Agent
    from app.services.agent.plan_service import deliver_plans

    async with async_session() as db:
        await _seed(db)
        agent = await db.get(Agent, 24)
        await _add_alarm(db)
        await deliver_plans(db, agent, "group:1")

        await _add_alarm(db, id=8, status="fired", task="写周报",
                         fired="now()", wake_at="now() - interval '1 hour'")
        events = await deliver_plans(db, agent, "group:1")
        assert len(events) == 1 and "✅" in events[0]["content"] and "写周报" in events[0]["content"]
        assert await deliver_plans(db, agent, "group:1") == []


async def test_other_conversation_plans_are_only_counted(migrated_db):
    """别的会话排的计划只报数：板子上不该出现别的会话的任务原文。"""
    from app.database import async_session
    from app.models.agent import Agent
    from app.services.agent.plan_service import deliver_plans

    async with async_session() as db:
        await _seed(db)
        agent = await db.get(Agent, 24)
        # 私信的会话键就是 session_id（"40_90" 这种），不是 "dm:3"
        await _add_alarm(db, id=9, task="私信里答应人家的暗号", origin="40_90", frame_id=None)

        events = await deliver_plans(db, agent, "group:1")
        assert len(events) == 1, events
        body = events[0]["content"]
        assert "其他状态还有 1 条未到点" in body
        assert "其他会话(40_90)" in body, body
        assert "私信里答应人家的暗号" not in body


async def test_board_sits_right_before_the_turn_messages(migrated_db):
    """位置契约：先投计划、再落当轮消息 → 板子紧挨着新消息之前（顺序是「先看板子，再读话」）。"""
    from app.database import async_session
    from app.models.agent import Agent
    from app.services.agent.plan_service import deliver_plans
    from app.services.history import history_service
    from app.services.history.context_sync import append_events
    from app.utils.pure.history import make_entry

    async with async_session() as db:
        await _seed(db)
        agent = await db.get(Agent, 24)
        await _add_alarm(db)

        await deliver_plans(db, agent, "group:1")
        await append_events(db, agent, "group:1", [
            make_entry("message", "在吗", actor="user", ref="1"),
        ])

        ledger = await history_service.read(db, agent.id, "group:1")
        kinds = [e["kind"] for e in ledger]
        assert kinds[-2:] == ["plan", "message"], kinds


async def _seed_group(db):
    """真库跑 build_messages 的最小现场：一个群、一个 AI、两条历史、一条计划。"""
    from sqlalchemy import text

    from db_reset import clear

    await clear(db, "agent_history_entries", "agent_alarms", "pending_messages", "messages",
                "group_members", "groups", "agents", "users")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES "
        "(1, '群主', 'x', 'human'), (2, '测试AI账号', 'x', 'ai')"))
    await db.execute(text(
        "INSERT INTO agents (id, owner_id, name, user_id, discoverable, state_stack, "
        "plan_injection_enabled) VALUES (1, 1, '测试AI', 2, true, :stack, true)"),
        {"stack": _FRAMES.replace("group:1", "group:59")})
    await db.execute(text(
        "INSERT INTO groups (id, name, owner_type, owner_id, avatar_mode, include_ai_in_avatar) "
        "VALUES (59, 'CoExisten', 'human', 1, 'default', true)"))
    await db.execute(text(
        "INSERT INTO group_members (group_id, member_type, member_id, role) VALUES "
        "(59, 'ai', 2, 'member'), (59, 'human', 1, 'owner')"))
    await db.execute(text(
        "INSERT INTO messages (id, group_id, sender_type, sender_id, content) VALUES "
        "(1, 59, 'human', 1, '第一句'), (2, 59, 'human', 1, '第二句')"))
    await db.execute(text(
        "INSERT INTO agent_alarms (id, agent_id, wake_at, task, status, created_at, "
        "origin_context_ref) VALUES (21, 1, now() + interval '2 hours', '收尾报告', "
        "'pending', now(), 'group:59')"))
    await db.commit()


async def test_plan_board_never_touches_the_locked_prefix(migrated_db):
    """计划板只许落在历史尾部：连续两次构建 message 0 必须逐字节相同，板子出现在历史之后。

    这条不是"顺手测一下"——开头只要有一个每轮会变的字节，后面整段历史的缓存就全废。
    """
    from sqlalchemy import select

    from app.ai.llm import build_messages
    from app.database import async_session
    from app.models.agent import Agent

    async with async_session() as db:
        await _seed_group(db)
        agent = (await db.execute(select(Agent).where(Agent.id == 1))).scalar_one()
        first = await build_messages(db, agent, 59)
        second = await build_messages(db, agent, 59)

        assert first[0]["content"] == second[0]["content"], "锁定前缀必须逐字节稳定"
        idx = next((i for i, m in enumerate(second)
                    if "## ⏰ 计划与闹钟" in (m.get("content") or "")), None)
        assert idx is not None and idx >= 1, [m["content"][:40] for m in second]
        assert "## ⏰ 计划与闹钟" not in first[0]["content"], "板子不许进锁定前缀（它每轮会变）"
        assert "收尾报告" in second[idx]["content"]


async def test_legacy_alarms_without_origin_are_not_claimed(migrated_db):
    """迁移前排的闹钟两列都是空的：不许猜成"这个会话的"，单独报数并把 id 给出来。"""
    from app.database import async_session
    from app.models.agent import Agent
    from app.services.agent.plan_service import deliver_plans

    async with async_session() as db:
        await _seed(db)
        agent = await db.get(Agent, 24)
        await _add_alarm(db, id=11, task="早先排的事", origin=None, frame_id=None)

        events = await deliver_plans(db, agent, "group:1")
        assert len(events) == 1, events
        body = events[0]["content"]
        assert "另有 1 条早先排的闹钟没记归属：#11" in body, body
        assert "本会话：" not in body and "早先排的事" not in body


async def test_restore_frame_rebuilds_with_the_right_type(migrated_db):
    """帧还在栈里就回跳（沿用原 id）；已被 pop 就重建，类型从会话键反推。"""
    from sqlalchemy import text

    from app.database import async_session
    from app.services.agent.state_stack_service import get_frames, restore_frame

    async with async_session() as db:
        await _seed(db)
        same = await restore_frame(db, 24, "frame-a", "group:1")
        assert same["id"] == "frame-a" and same["type"] == "group_chat"

        await db.execute(text("UPDATE agents SET state_stack = '[]'::jsonb WHERE id = 24"))
        await db.commit()
        dm = await restore_frame(db, 24, "", "40_90")
        assert dm["type"] == "dm" and dm["context_ref"] == "40_90", dm
        grp = await restore_frame(db, 24, "", "group:7")
        assert grp["type"] == "group_chat" and grp["context_ref"] == "group:7", grp

        frames = await get_frames(db, 24)
        assert [f["context_ref"] for f in frames] == ["40_90", "group:7"]
        assert frames[0]["status"] == "suspended" and frames[-1]["status"] == "active"


async def test_fired_receipt_stays_in_its_own_conversation(migrated_db):
    """别的会话执行过的计划不该出现在这个会话的板上；没归属的老行也不报。"""
    from app.database import async_session
    from app.models.agent import Agent
    from app.services.agent.plan_service import deliver_plans

    async with async_session() as db:
        await _seed(db)
        agent = await db.get(Agent, 24)
        await _add_alarm(db, id=31, status="fired", task="别的会话里的活",
                         origin="group:9", fired="now()", wake_at="now() - interval '1 hour'")
        await _add_alarm(db, id=32, status="fired", task="没归属的老活",
                         origin=None, frame_id=None, fired="now()", wake_at="now() - interval '2 hours'")
        assert await deliver_plans(db, agent, "group:1") == []


async def test_switch_off_delivers_nothing(migrated_db):
    """开关是"这个 AI 要不要看见自己的计划"的唯一入口。"""
    from app.database import async_session
    from app.models.agent import Agent
    from app.services.agent.plan_service import deliver_plans

    async with async_session() as db:
        await _seed(db, enabled=False)
        agent = await db.get(Agent, 24)
        await _add_alarm(db)
        assert await deliver_plans(db, agent, "group:1") == []
