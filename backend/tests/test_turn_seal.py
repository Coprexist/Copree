"""轮末封存：轮内的东西（工具轮历史 + 轮末结算）进账本

设计见 docs/dev/conversation_history.md §5：工具轮历史原本只活在当轮 messages 里，
轮一结束就没了——AI 下一轮只看得见消息，看不见自己干了什么。这里是它进账本的唯一入口。
"""
import pytest
from sqlalchemy import text

pytestmark = pytest.mark.anyio


async def _seed(db):
    from db_reset import clear

    await clear(db, "agents", "users", "messages", "groups", "group_members")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES "
        "(1, '测试用户', 'x', 'human'), (2, '测试AI账号', 'x', 'ai')"
    ))
    await db.execute(text(
        "INSERT INTO agents (id, owner_id, name, user_id, discoverable) "
        "VALUES (1, 1, '测试AI', 2, true)"
    ))
    await db.commit()


def test_handoff_is_never_compressed():
    """交接是「留给后面自己的」，压缩时必须原样搬运，不能揉进摘要"""
    from app.utils.pure.history import handoff_entry, is_compressible

    assert is_compressible(handoff_entry("改的是 executor")) is False


def test_tools_entry_renders_the_same_bytes_every_time():
    """同一批调用每次渲染必须字节一致（差一个字就是从这条起断缓存）"""
    from app.utils.pure.history import tools_entry

    items = [{"name": "send_gm", "note": "ok"}, {"name": "view_unread", "note": "失败：超时"}]
    assert tools_entry(items)["content"] == tools_entry(list(items))["content"]
    assert "send_gm(ok)" in tools_entry(items)["content"]


async def test_seal_turn_writes_tools_handoff_and_thinking(migrated_db):
    """轮末封存按顺序落三条：工具总账 → 交接 → 保留的思考"""
    from app.ai.executor import _seal_turn
    from app.database import async_session
    from app.models.agent import Agent
    from app.services.history import history_service as hs

    async with async_session() as db:
        await _seed(db)
        agent = await db.get(Agent, 1)
        ref = "group:999001"

        await _seal_turn(
            db, agent, group_id=999001, session_id=None, conversation_type="group",
            tool_log=[{"name": "send_gm", "note": "ok"},
                      {"name": "view_unread", "note": "失败：超时"}],
            settlement={"keep_thinking": True, "key_note": "  改的是 executor  "},
            reasoning="先看证据再动手",
        )
        await db.commit()

        entries = await hs.read(db, 1, ref)
        assert [e["kind"] for e in entries] == ["tool", "handoff", "thinking"]
        assert "send_gm(ok)" in entries[0]["content"] and "view_unread(失败：超时)" in entries[0]["content"]
        assert entries[1]["content"] == "[上一轮交接] 改的是 executor", "落库的是最终字节（去空白）"
        assert entries[2]["actor"] == "self"

        # 没工具、没交接、不留思考 → 一条都不写（不制造噪音条目）
        await _seal_turn(db, agent, group_id=999001, session_id=None, conversation_type="group",
                         tool_log=[], settlement={}, reasoning="")
        await db.commit()
        assert len(await hs.read(db, 1, ref)) == 3

        await hs.clear(db, 1, ref)
        await db.commit()

async def test_seal_turn_records_the_cutoff_honestly(migrated_db):
    """轮次用尽被平台收尾时补一条系统通知：上一轮不是 AI 自己收的尾

    少了它，后面的自己只看到一串工具名，会以为那轮已经张口说过。
    """
    from app.ai.executor import _seal_turn
    from app.database import async_session
    from app.models.agent import Agent
    from app.services.history import history_service as hs

    async with async_session() as db:
        await _seed(db)
        agent = await db.get(Agent, 1)
        ref = "group:999002"

        await _seal_turn(
            db, agent, group_id=999002, session_id=None, conversation_type="group",
            tool_log=[{"name": "web_fetch", "note": "ok"}],
            settlement={"keep_thinking": True},
            reasoning="在核版本号",
            cutoff="工具轮次用尽，本轮由平台结束，没走 end_turn。一条消息都没发出去。",
        )
        await db.commit()

        entries = await hs.read(db, 1, ref)
        assert [e["kind"] for e in entries] == ["tool", "notice", "thinking"],             [e["kind"] for e in entries]
        assert entries[1]["content"].startswith("[本轮收尾] 工具轮次用尽")
        assert entries[1]["actor"] == "system"

        await hs.clear(db, 1, ref)
        await db.commit()


async def test_sync_group_history_uses_the_shared_context_key(migrated_db):
    """群聊同步要能真的跑起来（会话键是关键字参数——踩过：位置调用直接 TypeError）"""
    from app.database import async_session
    from app.services.history.context_sync import sync_group_history

    async with async_session() as db:
        await _seed(db)
        from app.models.agent import Agent

        agent = await db.get(Agent, 1)
        await db.execute(text(
            "INSERT INTO groups (id, name, owner_type, owner_id, avatar_mode, include_ai_in_avatar) "
            "VALUES (64, '测试群', 'human', 1, 'default', true)"
        ))
        await db.execute(text(
            "INSERT INTO messages (group_id, sender_type, sender_id, content) VALUES "
            "(64, 'human', 1, '早'), (64, 'ai', 2, '早，什么事')"
        ))
        await db.commit()

        from app.services.history import history_service as hs0

        await hs0.clear(db, 1, "group:64")   # 先清上次跑剩下的（断言失败时收尾代码跑不到）
        entries = await sync_group_history(db, agent, 64, cap=10, max_len=256)
        await db.commit()

        kinds = [e["kind"] for e in entries]
        assert kinds[-2:] == ["message", "message"], kinds
        assert [e["actor"] for e in entries] == ["user", "self"], \
            [(e["kind"], e["actor"], e["ref"]) for e in entries]

        # 增量同步：after_id 有值时 get_gm_messages 返回**倒序**，必须按 id 归正（实测踩过）
        await db.execute(text(
            "INSERT INTO messages (group_id, sender_type, sender_id, content) "
            "VALUES (64, 'human', 1, '第二句')"
        ))
        await db.commit()
        again = await sync_group_history(db, agent, 64, cap=10, max_len=256)
        assert [e["actor"] for e in again] == ["user", "self", "user"], "新消息只能往后追加"
        assert "第二句" in again[-1]["content"]

        from app.services.history import history_service as hs

        await hs.clear(db, 1, "group:64")
        await db.commit()
