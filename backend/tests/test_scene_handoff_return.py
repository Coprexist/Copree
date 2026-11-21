"""会话消息把 AI 从插件帧里叫回来：状态要诚实，交接要带上「你刚才在别处做什么」

2026-11-01 之前：当前帧是插件帧 / AI 自己压的帧时，`ensure_active_frame` 直接不干预。
于是群里的消息在插件帧里被处理——日志按状态分桶落进插件桶、经历记成插件场景，
而那个回合实际是在回群里的人。有人在会话里叫你，你就回到那个会话；帧原地挂起，想再进
用 enter_state（帧上留着 doing，不上锁）。

插件帧没有对话原文可抄（tail 为空），所以交接的判据改成「有东西可讲」（handoff_pending），
没有原文时也渲染一行「刚才你在别处（刷 B 站）」。
"""
import pytest

pytestmark = pytest.mark.anyio

AGENT = 24


async def _seed(db):
    from sqlalchemy import text

    from db_reset import clear

    await clear(db, "agents", "users")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES (1, '甲', 'x', 'human')"))
    await db.execute(text(
        "INSERT INTO agents (id, owner_id, name, user_id, discoverable) "
        "VALUES (24, 1, '鲸鱼娘', 1, true)"))


async def test_conversation_message_pulls_ai_back_from_a_plugin_frame(migrated_db):
    from app.database import async_session
    from app.services.agent import state_stack_service as sss
    from app.utils.pure.state_stack import current, make_state_frame, running

    async with async_session() as db:
        await _seed(db)
        await sss.ensure_active_frame(db, AGENT, "group_chat", "group:1", "茶话会", "甲")
        await sss.push_state(
            db, AGENT, make_state_frame("bilibili_browse", "bilibili:24",
                                        label="刷 B 站", doing="去 B 站刷视频，回来报几条"))

        top = current(await sss._get_stack(db, AGENT))
        assert top and top["type"] == "bilibili_browse"

        # 群里有人说话 → 回到群会话帧；插件帧挂起，不结束
        await sss.ensure_active_frame(db, AGENT, "group_chat", "group:1", "茶话会", "甲")
        stack = await sss._get_stack(db, AGENT)
        top = current(stack)
        assert top and top["context_ref"] == "group:1", [f.get("type") for f in running(stack)]
        bili = next(f for f in stack if f.get("context_ref") == "bilibili:24")
        assert bili["status"] == "ended", "插件帧出链（记录留着，enter_state 能复活）"
        assert not any(f is bili for f in running(stack)), "同一时间只有当前会话帧在链上"
        assert top["completed_handoff"]["label"] == "刷 B 站"
        assert "刷视频" in (top["completed_handoff"].get("doing") or "")


async def test_handoff_without_tail_still_tells_where_you_left(migrated_db):
    from app.database import async_session
    from app.services.agent import state_stack_service as sss
    from app.utils.pure.state_stack import make_state_frame

    async with async_session() as db:
        await _seed(db)
        await sss.ensure_active_frame(db, AGENT, "group_chat", "group:1", "茶话会", "甲")
        await sss.push_state(
            db, AGENT, make_state_frame("bilibili_browse", "bilibili:24",
                                        label="刷 B 站", doing="去 B 站刷视频"))
        await sss.ensure_active_frame(db, AGENT, "group_chat", "group:1", "茶话会", "甲")

        # 插件帧没有对话原文（tail 为空），交接仍然要给：来源 + 在做什么
        block = await sss.frame_turn_context(db, AGENT, "group:1", [])
        assert "刚才你在别处" in block and "刷 B 站" in block and "刷视频" in block, block
        # 只注入一次
        assert await sss.frame_turn_context(db, AGENT, "group:1", []) == ""


def test_handoff_pending_does_not_require_tail():
    from app.utils.pure.state_stack import format_handoff_tail, handoff_pending

    assert handoff_pending({"tail": ["甲: 好"]}) is True
    assert handoff_pending({"from_context_ref": "bilibili:24"}) is True
    assert handoff_pending({"doing": "刷视频"}) is True
    assert handoff_pending({}) is False
    assert format_handoff_tail("刷 B 站", []) == "", "没有原文也没有 doing → 不渲染"
    assert "刚才你在别处" in format_handoff_tail("刷 B 站", [], doing="刷视频")
