"""会话历史账本：只追加 + 缺口在前 + 渲染即落库

设计见 docs/dev/conversation_history.md：
- 账本 = 模型看过的上下文的完整账本，段内只追加、只在解锁点重写；
- 缺口事件**写在这批消息之前、同批写入**（事后再插 = 改中段 = 断缓存）；
- 缺口只报条数（读原文用 read_conversation；补看机制不做）；
- 事件类（缺口/便签/通知）压缩时原样搬运，不揉进摘要。
"""
import pytest

pytestmark = pytest.mark.anyio


def test_gap_says_how_many_are_missing():
    from app.utils.pure.history import gap_entry, gap_text

    e = gap_entry(10, ref="m1")

    assert e["kind"] == "gap" and e["actor"] == "system"
    assert "还有 10 条" in e["content"] and "view_unread" in e["content"]
    assert e["content"] == gap_text(10), "只留一个文字来源，两处各写一遍必漂"


def test_entries_project_to_messages_by_actor():
    from app.utils.pure.history import entries_to_messages, make_entry

    entries = [
        make_entry("message", "书爱: 少宇", actor="user"),
        make_entry("message", "在，什么事", actor="self"),
        make_entry("gap", "（更早还有 3 条…）"),
    ]

    msgs = entries_to_messages(entries)

    assert [m["role"] for m in msgs] == ["user", "assistant", "system"]
    assert msgs[0]["content"] == "书爱: 少宇"


def test_events_are_never_compressible():
    from app.utils.pure.history import is_compressible, make_entry

    assert is_compressible(make_entry("message", "普通消息")) is True
    for kind in ("gap", "note", "notice"):
        assert is_compressible(make_entry(kind, "事件")) is False
    assert is_compressible(make_entry("message", "显式不可压", flags={"compressible": False})) is False
    assert is_compressible(make_entry("gap", "显式可压", flags={"compressible": True})) is True


async def _seed(db):
    from sqlalchemy import text
    from db_reset import clear

    await clear(db, "agents", "users")
    await db.execute(text("""
        INSERT INTO users (id, username, password_hash, type) VALUES
        (1, '测试用户', 'x', 'human'),
        (2, '测试AI账号', 'x', 'ai'),
        (3, '另一个AI账号', 'x', 'ai')
    """))
    await db.execute(text("""
        INSERT INTO agents (id, owner_id, name, user_id, discoverable)
        VALUES (1, 1, '测试AI', 2, true), (2, 1, '另一个AI', 3, true)
    """))
    await db.commit()


async def test_append_assigns_monotonic_seq_per_conversation(migrated_db):
    """seq 按会话各自递增：两个群互不干扰，且缺口与消息同批写入、缺口在前"""
    from app.database import async_session
    from app.utils.pure.history import gap_entry, make_entry
    from app.services.history import history_service as hs

    async with async_session() as db:
        await _seed(db)

        await hs.append(db, 1, "group:64", [
            gap_entry(10, ref="m1"),
            make_entry("message", "[#11] 书爱: 早", actor="user", ref="11"),
            make_entry("message", "[#30] 书爱: 少宇", actor="user", ref="30"),
        ])
        await hs.append(db, 1, "dm:1_40", [make_entry("message", "私信一条", actor="user")])
        await hs.append(db, 1, "group:64", [make_entry("message", "又一条", actor="user")])
        await db.commit()

        rows = await hs.read(db, 1, "group:64")
        assert [r["seq"] for r in rows] == [1, 2, 3, 4], "缺口在前、批次顺序即 seq 顺序"
        assert rows[0]["kind"] == "gap" and rows[-1]["content"] == "又一条"
        assert [r["seq"] for r in await hs.read(db, 1, "dm:1_40")] == [1], "另一个会话说它自己的 seq"
        assert await hs.count(db, 1, "group:64") == 4
        assert await hs.count(db, 2, "group:64") == 0, "账本按 AI 隔离"


async def test_read_since_seq_is_the_compact_boundary(migrated_db):
    """since_seq = compact 边界锚点：只取边界之后的部分"""
    from app.database import async_session
    from app.utils.pure.history import make_entry
    from app.services.history import history_service as hs

    async with async_session() as db:
        await _seed(db)
        await hs.append(db, 1, "group:64", [
            make_entry("message", f"第{i}条", actor="user") for i in range(1, 6)
        ])
        await db.commit()

        assert [r["seq"] for r in await hs.read(db, 1, "group:64", since_seq=3)] == [4, 5]
        assert await hs.last_seq(db, 1, "group:64") == 5


async def test_clear_is_the_unlock_rewrite(migrated_db):
    """解锁点重写：整段清掉，其它会话不受影响"""
    from app.database import async_session
    from app.utils.pure.history import make_entry
    from app.services.history import history_service as hs

    async with async_session() as db:
        await _seed(db)
        await hs.append(db, 1, "group:64", [make_entry("message", "群", actor="user")])
        await hs.append(db, 1, "dm:1_40", [make_entry("message", "私信", actor="user")])
        await db.commit()

        assert await hs.clear(db, 1, "group:64") == 1
        assert await hs.read(db, 1, "group:64") == []
        assert len(await hs.read(db, 1, "dm:1_40")) == 1


async def test_events_land_in_the_ledger_not_just_this_turn(migrated_db):
    """一次性事件（能力变更通知 / 便签撤下）要落成条目，且空内容不入账"""
    from app.database import async_session
    from app.services.history import history_service as hs
    from app.services.history.context_sync import append_events
    from app.utils.pure.history import make_entry

    assert await append_events(None, object(), "group:64", []) == [], "没有事件不碰 DB"
    assert await append_events(None, object(), "group:64",
                               [make_entry("notice", "   ")]) == [], "空内容不入账"

    async with async_session() as db:
        await _seed(db)
        written = await append_events(db, type("A", (), {"id": 1})(), "group:64",
                                      [make_entry("notice", "【能力变更通知】v1→v2")])
        await db.commit()

        assert [e["kind"] for e in written] == ["notice"]
        assert written[0]["seq"] == 1
        assert (await hs.read(db, 1, "group:64"))[0]["content"].startswith("【能力变更通知】")



async def test_rewrite_keeps_summary_events_and_tail(migrated_db):
    """解锁重写：摘要 + 事件原样搬运 + 最近 N 条（保留最新、不是最旧）；账本空则不动"""
    from app.database import async_session
    from app.services.history import history_service as hs
    from app.services.history.context_sync import rewrite_context
    from app.utils.pure.history import gap_entry, make_entry

    async with async_session() as db:
        await _seed(db)
        agent = type("A", (), {"id": 1})()
        await hs.append(db, 1, "group:64", [
            make_entry("message", "老的1", actor="user"),
            gap_entry(5, ref="m5"),
            make_entry("message", "老的2", actor="self"),
            make_entry("notice", "平台通知"),
            make_entry("message", "新的1", actor="user"),
            make_entry("message", "新的2", actor="self"),
        ])
        await db.commit()

        out = await rewrite_context(db, agent, "group:64", summary="[摘要] 前面聊了化学", keep_last=2)

        assert [e["kind"] for e in out] == ["summary", "gap", "notice", "message", "message"]
        assert [e["content"] for e in out[-2:]] == ["新的1", "新的2"], "保留最新的 N 条"
        assert [e["seq"] for e in out] == [1, 2, 3, 4, 5], "重写后 seq 从 1 重排"
        assert await hs.count(db, 1, "group:64") == 5
        assert await rewrite_context(db, agent, "group:none", summary="x", keep_last=2) == [], "账本空就不动"


def test_window_budget_counts_the_rendered_bytes():
    """字数预算按**渲染后**算：折过的长消息只占 2048 + 省略标记，不该按原文算"""
    from app.utils.pure.history import FOLD_LIMIT, fold_text, make_entry, take_newest_within

    folded = fold_text("字" * 30_000, limit=FOLD_LIMIT, expand_id=7)
    assert len(folded) < 3_000, len(folded)

    entries = [make_entry("message", f"短{i}", ref=str(i)) for i in (1, 2, 3)]
    entries.append(make_entry("message", folded, ref="4"))
    kept = take_newest_within(entries, max_chars=3_000)
    assert [e["ref"] for e in kept] == ["1", "2", "3", "4"], "折过的长消息不该把前面的挤掉（按原文算会）"

    huge = make_entry("message", "字" * 25_000, ref="9")
    assert take_newest_within([huge], max_chars=1_000) == [huge], "单条就超预算也要带上，否则水位卡死"


async def test_sync_window_counts_rendered_bytes(migrated_db):
    """真库闭环：一条 3 万字的消息折完后，窗口还有余量把前面那条短消息一起带进来"""
    from sqlalchemy import select, text

    from app.database import async_session
    from app.models.agent import Agent
    from app.services.history import context_sync

    async with async_session() as db:
        from db_reset import clear

        await clear(db, "agent_history_entries", "messages", "group_members", "groups", "agents", "users")
        await db.execute(text(
            "INSERT INTO users (id, username, password_hash, type) VALUES "
            "(1, '群主', 'x', 'human'), (2, 'AI账号', 'x', 'ai')"
        ))
        await db.execute(text(
            "INSERT INTO agents (id, owner_id, name, user_id, discoverable) "
            "VALUES (24, 1, '测试AI', 2, true)"
        ))
        await db.execute(text(
            "INSERT INTO groups (id, name, owner_type, owner_id, avatar_mode, include_ai_in_avatar) "
            "VALUES (64, '群', 'human', 1, 'default', true)"
        ))
        await db.execute(text(
            "INSERT INTO group_members (group_id, member_type, member_id, role) VALUES "
            "(64, 'human', 1, 'owner'), (64, 'ai', 2, 'member')"
        ))
        await db.execute(text(
            "INSERT INTO messages (group_id, sender_type, sender_id, content, created_at) VALUES "
            "(64, 'human', 1, '短消息', '2026-09-26 10:00:00')"
        ))
        await db.execute(text(
            "INSERT INTO messages (group_id, sender_type, sender_id, content, created_at) VALUES "
            "(64, 'human', 1, :big, '2026-09-26 10:01:00')"
        ), {"big": "字" * 30_000})
        await db.commit()

        agent = (await db.execute(select(Agent).where(Agent.id == 24))).scalar_one()
        old = context_sync.BATCH_MAX_CHARS
        context_sync.BATCH_MAX_CHARS = 3_000      # 旧口径下这条 3 万字的原文会把预算吃光
        try:
            entries = await context_sync.sync_group_history(db, agent, 64, cap=20, max_len=2048)
        finally:
            context_sync.BATCH_MAX_CHARS = old

        body = "\n".join(e["content"] for e in entries)
        assert "短消息" in body, "长消息折完后应该还有余量带上前面那条短的"
        assert "中间省略" in body, "长消息确实被折了"

