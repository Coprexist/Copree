"""便签投递 = 账本条目（投一次、撤下补通知、解锁离场）

设计见 docs/dev/conversation_history.md §4/§6：投递是一次性事件 → 落 note 条目（幂等）；
撤下不改已投出去的那条，只补一条通知；解锁（compact/清空）时两条一起离场（drop_on_unlock）。
"""
import json

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.anyio


async def _seed_with_note(db, note):
    from db_reset import clear

    await clear(db, "agents", "users")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES "
        "(1, '测试用户', 'x', 'human'), (2, '测试AI账号', 'x', 'ai')"
    ))
    frame = [{"id": "f1", "type": "group_chat", "status": "active", "context_ref": "group:64",
              "doing": "测试", "notes": []}]
    await db.execute(text(
        "INSERT INTO agents (id, owner_id, name, user_id, discoverable, cross_state_notes, "
        "llm_call_count, state_stack) VALUES (1, 1, '测试AI', 2, true, :notes, 0, :stack)"
    ), {"notes": json.dumps([note], ensure_ascii=False), "stack": json.dumps(frame, ensure_ascii=False)})
    await db.commit()


async def test_note_is_delivered_once_then_retired_then_dropped_on_unlock(migrated_db):
    from app.ai.llm import _deliver_frame_notes
    from app.database import async_session
    from app.models.agent import Agent
    from app.services.history import history_service as hs
    from app.services.history.context_sync import rewrite_context
    from app.utils.pure.cross_state_note import make_note

    async with async_session() as db:
        note = make_note("群里有人问暗号就答 7788", from_context_ref="40_90",
                         from_label="私信「书爱」", call_count=0)
        await _seed_with_note(db, note)
        agent = await db.get(Agent, 1)

        # ① 投递：一条 note 条目（幂等锚点是 ref=note:<id>）
        first = await _deliver_frame_notes(db, agent, "group:64", [])
        await db.commit()
        assert [e["kind"] for e in first] == ["note"]
        assert first[0]["ref"] == f"note:{note['id']}"
        assert "7788" in first[0]["content"] and first[0]["content"].startswith("[便签 · todo]")
        assert first[0]["flags"].get("drop_on_unlock") is True

        # ② 再投一次：账本里已经有了 → 一条都不写（幂等）
        assert await _deliver_frame_notes(db, agent, "group:64", first) == []

        # ③ 撤下：补一条通知（也标 drop_on_unlock），不改已投出去的那条
        stack = (await db.execute(text("select state_stack from agents where id=1"))).scalar()
        stack[-1]["notes"][0]["retired"] = True
        await db.execute(text("update agents set state_stack = :s where id=1"),
                         {"s": json.dumps(stack, ensure_ascii=False)})
        await db.commit()
        second = await _deliver_frame_notes(db, agent, "group:64", first)
        await db.commit()
        assert [e["kind"] for e in second] == ["notice"] and "撤下" in second[0]["content"]

        # ④ 解锁：便签 + 撤下通知一起离场（只活到解锁）
        for e in first + second:
            await hs.append(db, 1, "group:64", [e])
        await db.commit()
        assert [e["kind"] for e in await hs.read(db, 1, "group:64")] == ["note", "notice"]

        left = await rewrite_context(db, (await db.get(Agent, 1)), "group:64",
                                    summary="[摘要] 测试", keep_last=20)
        await db.commit()
        assert [e["kind"] for e in left] == ["summary"], "解锁后便签与撤下通知都该走干净"

        # ⑤ 但**整套解锁**（重写账本 + 清帧副本）之后，只要记录还在有效期内，该会话会**重新拿到**它
        #    ——便签的本意就是"40 次调用内，每个会话各投一份"
        from app.services.agent.state_stack_service import release_active_frame_notes

        await release_active_frame_notes(db, 1)
        await db.commit()
        again = await _deliver_frame_notes(db, agent, "group:64", await hs.read(db, 1, "group:64"))
        await db.commit()
        assert [e["kind"] for e in again] == ["note"], "解锁后有效期内该会话重新拿到便签"
