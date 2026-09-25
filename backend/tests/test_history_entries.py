"""会话历史账本：只追加 + 缺口在前 + 渲染即落库

设计见 docs/dev/conversation_history.md：
- 账本 = 模型看过的上下文的完整账本，段内只追加、只在解锁点重写；
- 缺口事件**写在这批消息之前、同批写入**（事后再插 = 改中段 = 断缓存）；
- 补看走 append 且带 [补] 抬头（顺序按写入位置，不按发生时间）；
- 事件类（缺口/补看/便签/通知）压缩时原样搬运，不揉进摘要。
"""
import pytest

pytestmark = pytest.mark.anyio


def test_gap_says_how_many_are_missing():
    from app.utils.pure.history import gap_entry, gap_text

    e = gap_entry(10, ref="m1")

    assert e["kind"] == "gap" and e["actor"] == "system"
    assert "还有 10 条" in e["content"] and "view_unread" in e["content"]
    assert e["content"] == gap_text(10), "只留一个文字来源，两处各写一遍必漂"


def test_backfill_header_says_these_are_older_messages():
    from app.utils.pure.history import backfill_header

    text = backfill_header(10, first_ref="1", last_ref="10", at="12:09")

    assert "#1.." in text and "更早" in text and "12:09" in text
    assert "不代表刚发生" in text, "补看排在末尾，必须说清先后"


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
    for kind in ("gap", "backfill", "note", "notice"):
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

