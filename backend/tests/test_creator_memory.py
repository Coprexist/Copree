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
        {"role": "tool", "tool_call_id": "c1", "content": "# 创建 AI 参考（很长的正文）"},
        {"role": "assistant", "content": FINAL_REPLY},
        {"role": "user", "content": "你记得你刚刚说了些什么吗？"},
    ]
    replayed = visible_messages(stored)

    assert [m["role"] for m in replayed] == ["user", "assistant", "user"], "一轮的两段 assistant 并成一条"
    assert "我先查一下文档。" in replayed[1]["content"] and FINAL_REPLY in replayed[1]["content"]
    assert not any(m["role"] == "tool" for m in replayed), "工具结果是过程，不重放"
    assert all("tool_calls" not in m for m in replayed), "没有 tool 应答的 tool_calls 会破坏请求体"
    assert not any(m["role"] == "system" for m in replayed), "system 每轮按当前快照重算"

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
