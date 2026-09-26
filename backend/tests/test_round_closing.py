"""轮次用尽时的收尾：读数、收尾轮、截断入账、has_output

线上实测的病：撞上 `max_tool_rounds` 后循环直接退出——不发言、不告警、日志标成有输出，
用户侧只看到「AI 不回我了」。这里把整条链跑一遍（LLM 打桩，走真实的 on_tool_call 派发，不走网络）。
"""
import asyncio

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.anyio

CFG = {"temperature": 0.8, "top_p": 0.9, "presence_penalty": 0.5,
       "frequency_penalty": 0.5, "thinking_enabled": False}


async def _seed(db):
    from db_reset import clear

    await clear(db, "agents", "users", "messages", "groups", "group_members")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES "
        "(1, '测试用户', 'x', 'human'), (2, '测试AI账号', 'x', 'ai')"
    ))
    await db.execute(text(
        "INSERT INTO agents (id, owner_id, name, user_id, discoverable, max_tool_rounds) "
        "VALUES (1, 1, '测试AI', 2, true, 2)"
    ))
    await db.execute(text(
        "INSERT INTO groups (id, name, owner_type, owner_id, avatar_mode, include_ai_in_avatar) "
        "VALUES (999003, '测试群', 'human', 1, 'default', true), "
        "(999004, '测试群二', 'human', 1, 'default', true)"
    ))
    # send_gm 要过群成员校验，不然「发出去了」根本没发生（测试就测了个假成功）
    await db.execute(text(
        "INSERT INTO group_members (group_id, member_type, member_id, role) VALUES "
        "(999003, 'ai', 2, 'member'), (999003, 'human', 1, 'owner'), "
        "(999004, 'ai', 2, 'member'), (999004, 'human', 1, 'owner')"
    ))
    await db.commit()


def _tool_call(name: str):
    return {
        "content": None,
        "reasoning_content": f"为什么调用 {name}",
        "tool_calls": [{
            "id": f"call_{name}",
            "type": "function",
            "function": {"name": name, "arguments": "{}"},
        }],
        "finish_reason": "tool_calls",
        "usage": {},
    }


def _tail(group_id: int) -> list[dict]:
    return [{"role": "system", "content": "## 当前时间\n2026-09-26 22:00 Asia/Shanghai\n"}]


async def test_exhausted_rounds_get_a_closing_round_and_an_honest_record(migrated_db):
    from app.ai import executor, llm
    from app.database import async_session
    from app.models.agent import Agent
    from app.services.history import history_service as hs

    calls: list[list[dict]] = []
    scripted = [_tool_call("list_states")] * 3

    async def fake_chat_completion(**kwargs):
        calls.append([dict(m) for m in kwargs["messages"]])
        resp = scripted[len(calls) - 1] if len(calls) <= len(scripted) else {
            "content": "查不到了", "finish_reason": "stop", "usage": {},
        }
        # 真实链路是流式回调里派发工具（on_tool_call），打桩也要走同一条路，
        # 否则工具既不执行也不进工具总账，测的就不是线上那条链
        for tc in resp.get("tool_calls") or []:
            await kwargs["on_tool_call"](tc)
        return resp

    original = llm.chat_completion
    llm.chat_completion = fake_chat_completion
    try:
        async with async_session() as db:
            await _seed(db)
            agent = await db.get(Agent, 1)
            ref = "group:999003"
            await hs.clear(db, 1, ref)
            await db.commit()

            messages = _tail(999003)
            await executor._tool_call_loop(
                db=db, agent=agent, group_id=999003, messages=messages, tools=[],
                model="test-model", api_base_url="http://test", api_key="k",
                max_loops=2, conversation_type="group", effective_cfg=CFG,
            )
            await db.commit()

            # ① 每轮都带读数，末段给收尾警告，收尾轮自称赠送轮
            readouts = [
                m["content"] for req in calls for m in req
                if isinstance(m.get("content"), str) and "## 本轮工具轮次" in m["content"]
            ]
            joined = "\n".join(readouts)
            assert "1/2" in joined and "2/2" in joined, readouts
            assert "还剩" in joined, "末段要给收尾警告，别让额度悄悄见底"
            assert "3/2（赠送轮，不占额度）" in joined, joined

            # ② 收尾轮只放行说话与结束：再开检索会被当场挡住
            # （挡下的是第三次调用，所以从被就地改写的 messages 里看）
            blocked = [m for m in messages
                       if isinstance(m.get("content"), str) and "收尾轮：工具轮次已用完" in m["content"]]
            assert blocked, "收尾轮里的检索调用必须被挡下，否则最后一轮也被烧掉"
            assert any(
                "[收尾] 工具轮次已经用完" in (m.get("content") or "")
                for req in calls for m in req
            ), "要有一轮明确告诉它可以说话了"

            # ③ 截断如实入账 + 没走 end_turn 时思考留痕
            entries = await hs.read(db, 1, ref)
            kinds = [e["kind"] for e in entries]
            assert "tool" in kinds and "notice" in kinds and "thinking" in kinds, kinds
            notice = [e for e in entries if e["kind"] == "notice"][0]
            assert notice["content"].startswith("[本轮收尾] 工具轮次用尽")
            assert "一条消息都没发出去" in notice["content"]

            # ④ 日志如实：一条消息都没发出去的轮次不是「有输出」
            row = (await db.execute(text(
                "SELECT has_output FROM ai_conversation_logs WHERE agent_id=1 ORDER BY id DESC LIMIT 1"
            ))).scalar_one()
            assert row is False

            await hs.clear(db, 1, ref)
            await db.commit()
    finally:
        llm.chat_completion = original

