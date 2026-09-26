"""消息撤回契约（用户 2026-09-26 定：站内也 2 分钟窗口，原文保留但不显示）。

为什么撤回不只是"打个标记"：账本是 append-only 的，AI 上下文里那条原文删不掉——
所以撤回的语义是"**再补一条作废通知**"；而"撤回之后才进历史"的那些只渲染占位。
只通知"账本里已经有这条"的 AI：没看过的以后拿到的本来就是占位，不用白补一条。
"""
from app.utils.pure.history import revoked_notice, revoked_text


def test_revoked_text_is_the_only_placeholder():
    assert "撤回" in revoked_text()


def test_revoked_notice_does_not_repeat_the_content():
    entry = revoked_notice("小明", 1358)
    assert entry["kind"] == "notice" and entry["actor"] == "system"
    assert "1358" in entry["content"] and "小明" in entry["content"]
    assert entry["ref"] == "revoked:1358"


def test_entry_renders_placeholder_for_revoked_message():
    from datetime import datetime

    from app.chat.gm import gm_message_entry

    class _Msg:
        id = 1358
        content = "这是被撤回的原文"
        sender_type = "human"
        sender_id = 7
        sender_name = None
        created_at = datetime(2026, 9, 26, 7, 30)
        revoked_at = datetime(2026, 9, 26, 7, 31)

    text = gm_message_entry(_Msg(), agent_name="化学老师", agent_user_id=40)["content"]
    assert revoked_text() in text and "这是被撤回的原文" not in text


async def _seed(db):
    from sqlalchemy import text

    from db_reset import clear

    await clear(db, "agent_history_entries", "pending_messages", "messages",
                "group_members", "groups", "agents", "users")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES "
        "(1, '群主', 'x', 'human'), (7, '小明', 'x', 'human'), "
        "(40, '化学老师', 'x', 'ai'), (41, '助教', 'x', 'ai')"
    ))
    await db.execute(text(
        "INSERT INTO agents (id, owner_id, name, user_id, discoverable) VALUES "
        "(24, 1, '化学老师', 40, true), (25, 1, '助教', 41, true)"
    ))
    await db.execute(text(
        "INSERT INTO groups (id, name, owner_type, owner_id, avatar_mode, include_ai_in_avatar) "
        "VALUES (64, '化学老师少宇群', 'human', 1, 'default', true)"
    ))
    await db.execute(text(
        "INSERT INTO group_members (group_id, member_type, member_id, role) VALUES "
        "(64, 'human', 1, 'owner'), (64, 'human', 7, 'member'), "
        "(64, 'ai', 40, 'member'), (64, 'ai', 41, 'member')"
    ))
    await db.execute(text(
        "INSERT INTO messages (id, group_id, sender_type, sender_id, content) VALUES "
        "(1358, 64, 'human', 7, '这是被撤回的原文')"
    ))
    await db.commit()


async def test_revoke_marks_and_notifies_only_agents_that_saw_it(migrated_db):
    from sqlalchemy import text

    from app.chat.revoke import revoke_group_message
    from app.database import async_session
    from app.models.message import Message
    from app.services.history import context_sync, history_service
    from app.utils.pure.history import make_entry

    async with async_session() as db:
        await _seed(db)
        # 只有 agent 24 的账本里有这条（agent 25 没看过 → 不该白补一条）
        await history_service.append(
            db, 24, context_sync.context_ref(group_id=64),
            [make_entry("message", "[09-26 07:30] 小明（id=7）: 这是被撤回的原文", actor="user", ref="1358")],
        )
        await db.commit()

        message = await db.get(Message, 1358)
        result = await revoke_group_message(db, message, actor_id=7)
        await db.commit()
        assert result["notified_agents"] == [24], result
        assert result["speaker"] == "小明"
        assert message.revoked_at is not None and message.revoked_by == 7

        rows = (await db.execute(text(
            "SELECT agent_id, kind, content FROM agent_history_entries "
            "WHERE context_ref = 'group:64' ORDER BY id"
        ))).all()
        assert [r[0] for r in rows] == [24, 24], rows
        assert rows[1][1] == "notice" and "1358" in rows[1][2], rows[1]
        assert (await db.execute(text(
            "SELECT count(*) FROM agent_history_entries WHERE agent_id = 25"
        ))).scalar() == 0


async def test_is_group_admin(migrated_db):
    from app.chat.gm import is_group_admin
    from app.database import async_session

    async with async_session() as db:
        await _seed(db)          # 1 = 群主(owner)，7 = 普通成员
        assert await is_group_admin(db, 64, 1) is True
        assert await is_group_admin(db, 64, 7) is False


async def test_dispatch_revoke_reports_every_channel():
    """撤回要**等**每个通道回答：成功 / 失败 / 不归我管（None）三种都能如实带上。"""
    from app.chat.outbound import dispatch_revoke, register_sink, registered_sinks, unregister_sink

    class _Msg:
        id = 1
        channel_msg_id = "REFIDX-1"

    async def ok(db, group_id, message):
        return {"channel": "ok", "ok": True}

    async def boom(db, group_id, message):
        raise RuntimeError("通道炸了")

    async def not_mine(db, group_id, message):
        return None                      # 这条不归它管 → 不进结果

    handles = [
        register_sink("test-revoke-ok", revoke=ok),
        register_sink("test-revoke-boom", revoke=boom),
        register_sink("test-revoke-mine", revoke=not_mine),
    ]
    try:
        assert "test-revoke-ok" in registered_sinks()["revoke"]
        results = await dispatch_revoke(None, 64, _Msg())
        assert {r["channel"]: r["ok"] for r in results} == {
            "ok": True, "test-revoke-boom": False,
        }, results
    finally:
        for handle in handles:
            unregister_sink(handle)


async def test_revoke_route_and_recall_tool(migrated_db):
    """路由与 AI 工具都走同一个服务：人对自己的消息能撤；AI 只能撤自己的。"""
    from fastapi import HTTPException

    from app.chat.gm import send_gm_message
    from app.database import async_session
    from app.routers.gm import revoke_gm_message
    from app.tools.chat_social.recall_message import RecallMessage

    async with async_session() as db:
        await _seed(db)
        result = await revoke_gm_message(
            group_id=64, message_id=1358, current_user={"user_id": 7, "username": "小明"}, db=db
        )
        assert result["revoked"] is True and result["speaker"] == "小明", result

        try:
            await revoke_gm_message(
                group_id=65, message_id=1358, current_user={"user_id": 7, "username": "小明"}, db=db
            )
            raise AssertionError("群对不上应该 404")
        except HTTPException as e:
            assert e.status_code == 404

        tool = RecallMessage()
        # 别人的消息：工具也撤不了（is_admin 恒 False）
        denied = await tool.execute(
            db, agent_id=24, group_id=64, arguments={"message_id": 1358}, context={}
        )
        assert denied.get("error") is True, denied

        own = await send_gm_message(db, group_id=64, sender_type="ai", sender_id=40, content="说错了")
        own_id = own.id
        await db.commit()
        ok = await tool.execute(db, agent_id=24, group_id=64, arguments={"message_id": own_id}, context={})
        assert ok["success"] is True and ok["message_id"] == own_id, ok


async def test_revoke_window_ownership_and_idempotency(migrated_db):
    from datetime import datetime, timedelta

    from app.chat.revoke import (
        REVOKE_WINDOW_SECONDS,
        RevokeDenied,
        RevokeExpired,
        revoke_group_message,
    )
    from app.database import async_session
    from app.models.message import Message

    async with async_session() as db:
        await _seed(db)
        message = await db.get(Message, 1358)

        try:
            await revoke_group_message(db, message, actor_id=1)          # 不是自己的
            raise AssertionError("别人的消息不该能撤")
        except RevokeDenied:
            pass

        message.created_at = datetime.utcnow() - timedelta(seconds=REVOKE_WINDOW_SECONDS + 5)
        try:
            await revoke_group_message(db, message, actor_id=7)          # 窗口过期
            raise AssertionError("过期不该能撤")
        except RevokeExpired:
            pass

        message.created_at = datetime.utcnow()                       # 管理员可撤别人的
        await db.flush()
        await revoke_group_message(db, message, actor_id=1, is_admin=True)
        assert message.revoked_at is not None and message.revoked_by == 1

        try:
            await revoke_group_message(db, message, actor_id=1, is_admin=True)
            raise AssertionError("不能撤两次")
        except RevokeDenied:
            pass
