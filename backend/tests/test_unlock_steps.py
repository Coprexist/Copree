"""解锁「整套」是把纪律变成断言：清单即契约，少一步就是半解锁

设计见 docs/dev/conversation_history.md §6：解锁必须整套（重写历史 + 复位思考保留标记 +
卸载最旧的图 + 清便签副本/条目）。Python 没有「你必须调过这个方法」的编译期保证，
所以这里用**测试**钉住：清单改了、或者函数没按清单跑，都当场红。
"""
import pytest
from sqlalchemy import text

pytestmark = pytest.mark.anyio


async def _seed(db):
    from db_reset import clear

    await clear(db, "agents", "users")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES "
        "(1, '测试用户', 'x', 'human'), (2, '测试AI账号', 'x', 'ai')"
    ))
    await db.execute(text(
        "INSERT INTO agents (id, owner_id, name, user_id, discoverable) "
        "VALUES (1, 1, '测试AI', 2, true)"
    ))
    await db.commit()


def test_unlock_steps_is_the_agreed_set():
    """改这份清单必须同时改这条测试——那道摩擦是故意的（它是设计文档里的契约）"""
    from app.ai.executor import UNLOCK_STEPS

    assert UNLOCK_STEPS == (
        "rewrite_history",
        "clear_note_copies",
        "reset_trigger_state",
        "apply_pending_config",
        "apply_pending_changes",
    )


async def test_unlock_context_runs_every_step_and_rewrites_the_ledger(migrated_db):
    """按清单顺序跑完，并且账本真的被重写（不然下一轮原文又回来了）"""
    from app.ai.executor import UNLOCK_STEPS, _unlock_context
    from app.database import async_session
    from app.models.agent import Agent
    from app.services.history import history_service as hs
    from app.utils.pure.history import make_entry

    async with async_session() as db:
        await _seed(db)
        agent = await db.get(Agent, 1)
        ref = "group:999001"
        await hs.append(db, 1, ref, [
            make_entry("message", "旧的1", actor="user"),
            make_entry("message", "旧的2", actor="self"),
        ])
        await db.commit()

        done = await _unlock_context(db, agent, group_id=999001, session_id=None,
                                     conversation_type="group", summary="[摘要] 测试")
        await db.commit()

        assert done == UNLOCK_STEPS, "执行的步骤必须与清单一致（顺序也算）"
        entries = await hs.read(db, 1, ref)
        assert entries and entries[0]["kind"] == "summary", "账本被重写成摘要在前"

        await hs.clear(db, 1, ref)
        await db.commit()

def test_one_shot_entries_carry_drop_on_unlock():
    """缺口 / 轮末交接：解锁时跟着上下文一起走（不然每次压缩都原样搬，越堆越多）"""
    from app.utils.pure.history import gap_entry, handoff_entry

    assert gap_entry(3).get("flags", {}).get("drop_on_unlock") is True
    assert handoff_entry("交接").get("flags", {}).get("drop_on_unlock") is True


async def test_unlock_drops_one_shot_entries_and_resets_trigger_state(migrated_db):
    """解锁后：缺口/交接/通知离场、消息与摘要留下；会话帧上的触发规则状态从零开始"""
    from app.ai.executor import _unlock_context
    from app.database import async_session
    from app.models.agent import Agent
    from app.services.agent.state_stack_service import (
        ensure_active_frame, load_trigger_state, save_trigger_state,
    )
    from app.services.history import history_service as hs
    from app.utils.pure.history import gap_entry, handoff_entry, make_entry

    async with async_session() as db:
        await _seed(db)
        ref = "group:999002"
        await ensure_active_frame(db, 1, "group_chat", ref, "测试群", "小明")
        await save_trigger_state(db, 1, {
            "tool_uses": {"web_search": 1},
            "delivered": {"web_search.verify_after_reply": True},
        })
        await hs.append(db, 1, ref, [
            gap_entry(3, ref="1"),
            handoff_entry("上一轮在改文档"),
            make_entry("notice", "[能力变更通知] 测试", flags={"drop_on_unlock": True}),
            make_entry("message", "最近一条", actor="user"),
        ])
        await db.commit()

        agent = await db.get(Agent, 1)
        await _unlock_context(db, agent, group_id=999002, session_id=None,
                              conversation_type="group", summary="[摘要] 测试")
        await db.commit()

        kinds = [e["kind"] for e in await hs.read(db, 1, ref)]
        assert kinds[0] == "summary", kinds
        assert "gap" not in kinds and "handoff" not in kinds and "notice" not in kinds, kinds
        assert "message" in kinds, kinds
        assert await load_trigger_state(db, 1) == {"tool_uses": {}, "delivered": {}}

        await hs.clear(db, 1, ref)
        await db.commit()