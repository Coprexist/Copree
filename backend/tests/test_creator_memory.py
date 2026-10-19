"""创建助手的记忆：存档里必须留着助手自己说过的话

背景（2026-10-19，草稿 16）：第二轮问「你记得你刚刚说了些什么吗？」，助手答
「我这边没有上一轮或者更早聊天的记录」——它确实没看到任何回答。根因是 _run_turn
只把带 tool_calls 的 assistant 消息追加进 messages，最后那段收尾正文只写进了 SSE
流和 text_out，没进存档。下一轮 load_transcript 取到的就是一串没有回答的 user 消息，
模型只能重新查一遍文档再总结一遍。
"""
import pytest

pytestmark = pytest.mark.anyio

USER_ID = 9711
FINAL_REPLY = "文档讲的是人设四层：身份、关系、说话方式、边界。"


class _Patch:
    """自带 runner 没有 monkeypatch：就地换属性，退出时换回来"""

    def __init__(self):
        self._undo: list = []

    def set(self, obj, name: str, value) -> None:
        self._undo.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        for obj, name, old in reversed(self._undo):
            setattr(obj, name, old)


def test_visible_messages_keeps_spoken_prose_and_drops_tool_plumbing():
    from app.services.creator.agent_creator_service import visible_messages

    stored = [
        {"role": "system", "content": "你是创建助手"},
        {"role": "user", "content": "你创建AI有专业参考文档吗"},
        {"role": "assistant", "content": "我先查一下文档。",
         "tool_calls": [{"id": "c1", "type": "function",
                         "function": {"name": "read_manual", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "# 创建 AI 参考\n" + "长正文" * 800},
        {"role": "assistant", "content": FINAL_REPLY},
        {"role": "user", "content": "你记得你刚刚说了些什么吗？"},
    ]
    replayed = visible_messages(stored)

    # 工具轮整组保留：assistant(tool_calls) + 它的 tool 回复，顺序与原样一致
    assert [m["role"] for m in replayed] == ["user", "assistant", "tool", "assistant", "user"]
    assert replayed[1]["tool_calls"][0]["id"] == "c1" and replayed[2]["tool_call_id"] == "c1"
    assert "长正文" in replayed[2]["content"], "查到的结果必须留在上下文里"
    assert replayed[2]["content"].endswith("…（结果太长，已截断）"), "单条结果按上限截断"
    assert len(replayed[2]["content"]) <= 800 + len("…（结果太长，已截断）")
    assert replayed[1]["content"] == "我先查一下文档。", "先说一句再调工具，这句留着"
    assert replayed[3]["content"] == FINAL_REPLY
    assert not any(m["role"] == "system" for m in replayed), "system 每轮按当前快照重算"

    # 没有回复的调用要剔掉：悬空 tool_call_id 会被 API 直接 400
    orphan = visible_messages([
        {"role": "user", "content": "查"}, 
        {"role": "assistant", "content": "查一下",
         "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "web_search", "arguments": "{}"}},
                        {"id": "c2", "type": "function", "function": {"name": "read_manual", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "只有这一条回来了"},
        {"role": "assistant", "content": "查完了"},
    ])
    calls = [c for m in orphan if m.get("tool_calls") for c in m["tool_calls"]]
    assert [c["id"] for c in calls] == ["c1"], "没有回复的调用剔掉"
    assert [m.get("tool_call_id") for m in orphan if m["role"] == "tool"] == ["c1"]

    # 只带最近两轮的工具结果：更早的轮次只留助手说出口的话，别让几千字文档每轮都重付一次
    def _turn(n: int) -> list[dict]:
        return [
            {"role": "user", "content": f"第{n}轮"},
            {"role": "assistant", "content": f"查{n}",
             "tool_calls": [{"id": f"c{n}", "type": "function",
                             "function": {"name": "read_manual", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": f"c{n}", "content": f"第{n}轮的结果"},
            {"role": "assistant", "content": f"第{n}轮答完了"},
        ]

    long_history = visible_messages(_turn(1) + _turn(2) + _turn(3))
    assert [m["role"] for m in long_history].count("tool") == 2, "最近两轮的工具结果在，更早的不在"
    assert "第1轮的结果" not in [m.get("content") for m in long_history if m["role"] == "tool"]
    assert any(m.get("content") == "第1轮答完了" for m in long_history), "旧轮次的话还留着"

    # 用户一句一句打的，不并：并了就分不清轮次（历史日志里连着几条 user 是缺回答的旧账，
    # 页面上也是几个气泡）
    back = visible_messages([
        {"role": "user", "content": "第一句"},
        {"role": "user", "content": "第二句"},
        {"role": "assistant", "content": "一句回答"},
    ])
    assert [m["content"] for m in back] == ["第一句", "第二句", "一句回答"]


async def test_turn_snapshot_keeps_the_final_reply(migrated_db):
    """走真实的一轮：先调工具、再收尾，存档里必须留得住那句收尾回复"""
    import app.services.agent.llm_credentials as credentials_module
    import app.services.content.conversation_log_service as log_service
    import app.services.world.world_chat_service as world_chat
    from app.database import async_session
    from app.models.user import User
    from app.services.creator import agent_creator_service as creator

    async with async_session() as db:
        db.add(User(id=USER_ID, username="creator-memory", password_hash="x", type="human"))
        await db.commit()
        draft = await creator.create_draft(db, USER_ID)

        rounds = [
            {"content": "我先查一下文档。", "reasoning": "", "usage": None,
             "tool_calls": [{"id": "c1", "name": "read_manual", "arguments": "{}"}]},
            {"content": FINAL_REPLY, "reasoning": "", "usage": None, "tool_calls": []},
        ]
        saved: list[dict] = []

        async def fake_stream(model, api_base, api_key, messages, tools, out):
            out.clear()
            out.update(rounds.pop(0))
            if False:  # 必须是 async generator，但这一轮不吐 SSE
                yield ""

        async def fake_tool(db_, draft_, name, args):
            return "文档正文", None

        async def fake_save(repo, agent_id, messages, **kwargs):
            saved.extend(dict(m) for m in messages)
            return 1

        async def fake_credentials(db_, user_id):
            return None, "http://llm.test"

        async def fake_model(db_, api_base, *, owner_id):
            return "test-model"

        with _Patch() as patch:
            patch.set(creator, "_stream_once", fake_stream)
            patch.set(creator, "_run_tool", fake_tool)
            patch.set(creator, "creator_tools", lambda: [])
            patch.set(log_service, "save_conversation_log", fake_save)
            patch.set(credentials_module, "resolve_user_credentials", fake_credentials)
            patch.set(world_chat, "resolve_chat_model", fake_model)

            async for _ in creator._run_turn(db, USER_ID, draft, "你创建AI有专业参考文档吗"):
                pass

        assert saved, "这一轮必须存档"
        assert saved[-1] == {"role": "assistant", "content": FINAL_REPLY}, "收尾回复要进快照"

        # 第二轮拿到的上下文里就有这句回答——不再是一串没有回答的 user 消息
        history = creator.visible_messages(saved)
        assert history[-1]["role"] == "assistant"
        assert FINAL_REPLY in history[-1]["content"], "下一轮看得到这句回答"
        assert history[0] == {"role": "user", "content": "你创建AI有专业参考文档吗"}


CLOSING = "工具轮用满了，先说下现在填到哪一步：人设还在改。"


async def test_round_cap_spends_one_free_closing_round(migrated_db):
    """工具轮用尽后白送一轮（这一轮不递工具）：快照必须以 assistant 收尾，否则下一轮又失忆"""
    import app.services.agent.llm_credentials as credentials_module
    import app.services.content.conversation_log_service as log_service
    import app.services.world.world_chat_service as world_chat
    from app.database import async_session
    from app.models.user import User
    from app.services.creator import agent_creator_service as creator

    async with async_session() as db:
        db.add(User(id=USER_ID + 1, username="creator-cap", password_hash="x", type="human"))
        await db.commit()
        draft = await creator.create_draft(db, USER_ID + 1)

        rounds = [
            {"content": "", "reasoning": "", "usage": None,
             "tool_calls": [{"id": f"c{i}", "name": "read_manual", "arguments": "{}"}]}
            for i in range(creator.MAX_TOOL_ROUNDS)
        ] + [{"content": CLOSING, "reasoning": "", "usage": None, "tool_calls": []}]
        tools_seen: list[list] = []
        saved: list[dict] = []

        async def fake_stream(model, api_base, api_key, messages, tools, out):
            tools_seen.append(list(tools))
            out.clear()
            out.update(rounds.pop(0))
            if False:
                yield ""

        async def fake_tool(db_, draft_, name, args):
            return "文档正文", None

        async def fake_save(repo, agent_id, messages, **kwargs):
            saved.extend(dict(m) for m in messages)
            return 1

        async def fake_credentials(db_, user_id):
            return None, "http://llm.test"

        async def fake_model(db_, api_base, *, owner_id):
            return "test-model"

        with _Patch() as patch:
            patch.set(creator, "_stream_once", fake_stream)
            patch.set(creator, "_run_tool", fake_tool)
            patch.set(creator, "creator_tools", lambda: [{"type": "function"}])
            patch.set(log_service, "save_conversation_log", fake_save)
            patch.set(credentials_module, "resolve_user_credentials", fake_credentials)
            patch.set(world_chat, "resolve_chat_model", fake_model)

            events = [ev async for ev in creator._run_turn(db, USER_ID + 1, draft, "帮我建一个逻各斯")]

        assert rounds == [], "工具轮用尽后必须还有一次收尾调用"
        assert tools_seen[:-1] and all(t for t in tools_seen[:-1]), "工具轮照旧带工具"
        assert tools_seen[-1] == [], "收尾轮不递工具（模型只能把话说完）"
        assert saved[-1] == {"role": "assistant", "content": CLOSING}, "收尾轮正文要进快照"
        assert any("工具轮次到上限" in ev for ev in events), "用满轮次要如实告诉用户"
