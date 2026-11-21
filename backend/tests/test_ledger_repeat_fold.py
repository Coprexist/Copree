"""账本里重复的事件条目：写入时就别落（不改老条目）。

线上实测：一段会话里 `[本轮工具] send_gm(ok)；end_turn(ok)` 落了 59 条一模一样的，
channel_mode 通知同 ref 9 条。压缩时事件条目原样搬运、折不掉，同一句话把上下文越撑越长。
判据放在写入侧（append_events）：账本只追加，压缩时清理等于改写中段，前缀缓存整段作废。
"""
import pytest
from sqlalchemy import text

from app.utils.pure.history import fold_repeats, make_entry

pytestmark = pytest.mark.anyio


def _tool(text_: str) -> dict:
    return make_entry("tool", f"[本轮工具] {text_}", actor="self")


def test_same_tool_summary_twice_lands_once():
    entries = [_tool("send_gm(ok)；end_turn(ok)"), _tool("send_gm(ok)；end_turn(ok)")]
    assert fold_repeats(entries, {}) == entries[:1]


def test_changed_tool_summary_is_kept():
    entries = [_tool("send_gm(ok)"), _tool("send_gm(ok)；set_alarm(ok)")]
    assert fold_repeats(entries, {}) == entries


def test_last_entry_from_the_ledger_counts():
    """中间夹了别的条目也算：比对的是同 kind 同 ref 的**上一次**，不是上一条。"""
    entries = [_tool("send_gm(ok)")]
    assert fold_repeats(entries, {("tool", ""): "[本轮工具] send_gm(ok)"}) == []


def test_notices_fold_only_with_same_ref():
    a = make_entry("notice", "本群改成只 @ 我", ref="channel_mode:at")
    b = make_entry("notice", "本群改成只 @ 我", ref="channel_mode:at")
    c = make_entry("notice", "本群改成只 @ 我", ref="channel_mode:full")
    assert fold_repeats([a, b, c], {}) == [a, c], "不同 ref 是两件事，不许并"


def test_content_repeats_are_not_folded():
    """消息、缺口、交接重复就是内容真的重复，吞掉会丢东西。"""
    entries = [make_entry("message", "在吗"), make_entry("message", "在吗"),
               make_entry("gap", "（更早还有 3 条未读消息）"),
               make_entry("gap", "（更早还有 3 条未读消息）")]
    assert fold_repeats(entries, {}) == entries


async def _seed(db):
    from db_reset import clear

    await clear(db, "agent_history_entries", "agents", "users")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES (1, '群主', 'x', 'human')"))
    await db.execute(text(
        "INSERT INTO agents (id, owner_id, name, discoverable, ai_type) "
        "VALUES (1, 1, '测试AI', true, 'resonance')"))
    await db.commit()


async def test_append_events_does_not_write_the_same_line_again(migrated_db):
    from app.database import async_session
    from app.models.agent import Agent
    from app.services.history import history_service
    from app.services.history.context_sync import append_events

    async with async_session() as db:
        await _seed(db)
        agent = await db.get(Agent, 1)
        event = _tool("send_gm(ok)")

        first = await append_events(db, agent, "group:7", [event])
        assert len(first) == 1, "第一次当然要落"

        again = await append_events(db, agent, "group:7", [_tool("send_gm(ok)")])
        assert again == [], "一字不差的重复摘要不再落条目"
        rows = await history_service.read(db, 1, "group:7")
        assert len([r for r in rows if r["kind"] == "tool"]) == 1

        changed = await append_events(db, agent, "group:7", [_tool("send_gm(ok)；set_alarm(ok)")])
        assert len(changed) == 1, "组合变了就是新信息"
