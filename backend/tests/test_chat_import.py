"""
从聊天记录导入：统一结构 → 私信历史。

三条本次拍板的语义，测试直接钉住：

1. **进历史**：导入的对话是该 AI 与创建者之间真实存在的私信消息，不是只读档案；
2. **`via='import'`**：与 `via='qq'` 同一套通道标记，界面上的「来自导入」只认它；
3. **无原文件要如实说**：拿不到原图的附件标 `available=False`，不假装有。
"""
import json

import pytest
from sqlalchemy import select, text

pytestmark = pytest.mark.anyio

OWNER, AGENT_USER, AGENT_ID = 990001, 990002, 990003


async def _seed(db):
    from db_reset import clear

    await clear(db, "agents", "users")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES "
        "(:owner, '主人', 'x', 'human'), (:au, '导入AI', 'x', 'ai')"),
        {"owner": OWNER, "au": AGENT_USER})
    await db.execute(text(
        "INSERT INTO agents (id, owner_id, name, user_id, state) "
        "VALUES (:a, :o, '导入AI', :u, 'active')"),
        {"a": AGENT_ID, "o": OWNER, "u": AGENT_USER})
    await db.commit()


SHARE = {
    "data": {
        "biz_code": 0,
        "biz_data": {
            "title": "装修方案",
            "messages": [
                {"message_id": 1, "parent_id": None, "role": "USER", "inserted_at": 1764156000,
                 "fragments": [{"type": "REQUEST", "content": "帮我列个装修预算"}]},
                {"message_id": 2, "parent_id": 1, "role": "ASSISTANT", "inserted_at": 1764156060,
                 "fragments": [{"type": "RESPONSE", "content": "好的，按 90 平算"},
                               {"type": "THINK", "content": "他在问装修"}]},
                {"message_id": 3, "parent_id": 2, "role": "USER", "inserted_at": 1764156120,
                 "fragments": [
                     {"type": "FILE", "files": [{"file_name": "照片2.jpg", "file_size": 1258291,
                                                 "content_type": "image/jpeg"}]},
                     {"type": "REQUEST", "content": "这张图看看"}]},
            ],
        },
    }
}


def test_share_payload_becomes_transcript():
    """分享 JSON → 统一结构：思考片段不进正文，附件只留元数据。"""
    from app.services.chat_import import parsers

    t = parsers.parse(json.dumps(SHARE))

    assert t.origin.parser == "deepseek.share"
    assert [m.role for m in t.messages] == ["user", "assistant", "user"]
    assert t.messages[1].content == "好的，按 90 平算"
    att = t.messages[2].attachments[0]
    assert (att.name, att.available, att.reason) == ("照片2.jpg", False, "no_source")
    assert t.stats()["missing_attachments"] == 1


def test_pasted_text_keeps_roles():
    """粘贴文本：认角色前缀，连续行归同一段。"""
    from app.services.chat_import import parsers

    t = parsers.parse("用户：第一问\nAI：第一答\n还在说\n用户：第二问")
    assert [m.role for m in t.messages] == ["user", "assistant", "user"]
    assert t.messages[1].content == "第一答\n还在说"


# ── 智能整理 ────────────────────────────────────────────────────────

def test_organize_parses_fenced_json():
    """模型输出经常套代码块：只认第一个 JSON 对象，前后废话不要。"""
    from app.services.chat_import.organize import _parse_json

    raw = '好的，整理如下：\n\n```json\n{"prompt": "你是小满", "memories": [], "summary": "聊过"}\n```\n希望有帮助'
    assert _parse_json(raw)["prompt"] == "你是小满"


def test_organize_cleans_shape():
    """类型不对的丢掉而不是猜：importance 越界钳住，空内容不要。"""
    from app.services.chat_import.organize import _clean

    out = _clean({
        "prompt": " 你是小满 ",
        "memories": [
            {"content": "用户怕冷", "importance": 9},
            {"content": "  ", "importance": 3},
            "用户住在杭州",
            {"nope": 1},
        ],
        "knowledge": [
            {"title": "世界观", "summary": "设定", "content": "正文"},
            {"title": "", "content": "没标题"},
        ],
        "summary": "聊过装修",
    })
    assert out["prompt"] == "你是小满"
    assert [m["content"] for m in out["memories"]] == ["用户怕冷", "用户住在杭州"]
    assert out["memories"][0]["importance"] == 5      # 9 被钳到 5
    assert len(out["knowledge"]) == 1
    assert out["summary"] == "聊过装修"


def test_organize_digest_keeps_head_and_tail():
    """超长对话均匀采样，但第一段与最后一段一定在——人设线索和最近的相处都在两头。"""
    from app.services.chat_import.organize import DIGEST_MAX_MESSAGES, _digest
    from app.services.chat_import.schema import Transcript, Turn

    msgs = [Turn(role="user" if i % 2 == 0 else "assistant", content=f"第{i}句")
            for i in range(DIGEST_MAX_MESSAGES * 3)]
    body = _digest(Transcript(messages=msgs))
    assert "第0句" in body
    assert f"第{len(msgs) - 1}句" in body
    assert body.count("：") <= DIGEST_MAX_MESSAGES


async def test_organize_without_config_asks_for_config(migrated_db):
    """没配模型时不报错、不偷偷换 Key：抛 OrganizeUnavailable 让前端弹「去配置」。"""
    from app.database import async_session
    from app.services.chat_import import parsers
    from app.services.chat_import.organize import OrganizeUnavailable, organize

    async with async_session() as db:
        await _seed(db)
        transcript = parsers.parse("用户：hi\nAI：hi")
        try:
            await organize(db, user_id=OWNER, transcript=transcript)
        except OrganizeUnavailable as e:
            assert e.reason in (
                    "no_route", "no_quota", "no_default", "not_configured", "no_member"
                )
        else:
            raise AssertionError("测试库没有配置，不该整理成功")


async def test_organize_end_to_end(migrated_db, monkeypatch):
    """配置可用时：一次调用 → 四样产出，且提示词/记忆/知识库互不串味。"""
    import httpx
    from types import SimpleNamespace

    from app.services.chat_import import organize as organize_mod

    fake_resolution = SimpleNamespace(
        usable=True, base_url="https://api.example.com/v1", api_key="sk-test",
        chat_model="test-model", models=["test-model"], source="default",
        missing_reason="ok",
    )

    async def fake_resolve(db, user_id, **kw):
        return fake_resolution

    monkeypatch.setattr(organize_mod, "resolve_user_llm", fake_resolve)

    answer = (
        '整理好了：\n```json\n'
        '{"prompt": "你是小满，讲话短。", '
        '"memories": [{"content": "用户住在杭州", "importance": 4}], '
        '"knowledge": [{"title": "世界观", "summary": "设定", "content": "正文"}], '
        '"summary": "之前聊过装修"}\n```'
    )
    seen = {}

    class FakeResp:
        status_code = 200
        text = ""
        def json(self):
            return {"choices": [{"message": {"content": answer}}]}

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, json=None, headers=None):
            seen["url"], seen["payload"], seen["headers"] = url, json, headers
            return FakeResp()

    monkeypatch.setattr(organize_mod.httpx, "AsyncClient", FakeClient)

    from app.database import async_session
    from app.services.chat_import import parsers

    async with async_session() as db:
        await _seed(db)
        transcript = parsers.parse("用户：我在杭州\nAI：好的")
        out = await organize_mod.organize(db, user_id=OWNER, transcript=transcript)

    assert out["prompt"].startswith("你是小满")
    assert out["memories"][0]["content"] == "用户住在杭州"
    assert out["knowledge"][0]["title"] == "世界观"
    assert out["summary"] == "之前聊过装修"
    assert out["model"] == "test-model" and out["source"] == "default"
    # 打的是 OpenAI 兼容口径：/chat/completions，带 Bearer
    assert seen["url"] == "https://api.example.com/v1/chat/completions"
    assert seen["headers"]["Authorization"] == "Bearer sk-test"
    assert seen["payload"]["stream"] is False


async def test_import_writes_dm_history(migrated_db):
    """导入 → DM 历史里是真消息：via='import'、发送者按角色、时间还原、已读。"""
    from app.database import async_session
    from app.models.agent import Agent
    from app.models.chat_import import ChatImport
    from app.models.dm import DMMessage
    from app.services.chat_import import parsers
    from app.services.chat_import.importer import import_transcript

    async with async_session() as db:
        await _seed(db)
        agent = (await db.execute(select(Agent).where(Agent.id == AGENT_ID))).scalars().one()
        transcript = parsers.parse(json.dumps(SHARE))

        result = await import_transcript(
            db, agent=agent, creator_user_id=OWNER, transcript=transcript
        )
        await db.commit()

        rows = (await db.execute(
            select(DMMessage).order_by(DMMessage.id)
        )).scalars().all()

        assert result["imported"] == 3
        assert [r.sender_id for r in rows] == [OWNER, AGENT_USER, OWNER]
        assert all(r.via == "import" for r in rows)
        assert all(r.read_at is not None for r in rows)      # 历史不该堆未读
        assert rows[0].content == "帮我列个装修预算"
        # 原始时间被还原（不是导入时刻）
        assert rows[0].created_at < rows[-1].created_at
        # 无原文件在消息里如实标着
        att = json.loads(rows[-1].attachments)[0]
        assert att["available"] is False and att["reason"] == "no_source"

        record = (await db.execute(select(ChatImport))).scalars().one()
        assert record.message_count == 3
        assert record.missing_attachment_count == 1
        assert record.platform == "deepseek"


async def test_initial_summary_lands_in_ledger(migrated_db):
    """「初步摘要注入」：导入时往账本落一条 kind=summary，位置在开场之后、历史之前。

    这条摘要就是将来压缩时要被顶掉的那条——所以它必须真的在账本里，而不是只存在
    chat_imports 的字段里（那样压缩时无处可释放，也不会被 AI 读到）。
    """
    from app.database import async_session
    from app.models.agent import Agent
    from app.services.chat_import import parsers
    from app.services.chat_import.importer import import_transcript
    from app.services.history import history_service
    from app.services.history.context_sync import context_ref

    async with async_session() as db:
        await _seed(db)
        agent = (await db.execute(select(Agent).where(Agent.id == AGENT_ID))).scalars().one()
        transcript = parsers.parse(json.dumps(SHARE))
        transcript.summary = "2026-11-26 我们在 DeepSeek 聊过装修预算，最后定了 90 平的方案。"

        result = await import_transcript(
            db, agent=agent, creator_user_id=OWNER, transcript=transcript
        )
        await db.commit()

        assert result["summary_injected"] is True
        # DM 会话键：AI 的虚拟账号与创建者的排序拼接
        ref = context_ref(session_id=f"{min(OWNER, AGENT_USER)}_{max(OWNER, AGENT_USER)}")
        entries = await history_service.read(db, AGENT_ID, ref)
        summaries = [e for e in entries if e.get("kind") == "summary"]
        assert len(summaries) == 1
        assert "装修预算" in summaries[0]["content"]


async def test_no_summary_no_entry(migrated_db):
    """没给摘要就不落——不写一条没内容的占位条目，账本里不留噪声。"""
    from app.database import async_session
    from app.models.agent import Agent
    from app.services.chat_import import parsers
    from app.services.chat_import.importer import import_transcript
    from app.services.history import history_service
    from app.services.history.context_sync import context_ref

    async with async_session() as db:
        await _seed(db)
        agent = (await db.execute(select(Agent).where(Agent.id == AGENT_ID))).scalars().one()
        transcript = parsers.parse(json.dumps(SHARE))   # summary 为空

        result = await import_transcript(
            db, agent=agent, creator_user_id=OWNER, transcript=transcript
        )
        await db.commit()

        assert result["summary_injected"] is False
        ref = context_ref(session_id=f"{min(OWNER, AGENT_USER)}_{max(OWNER, AGENT_USER)}")
        entries = await history_service.read(db, AGENT_ID, ref)
        assert not [e for e in entries if e.get("kind") == "summary"]


async def test_apply_organize_writes_memories_and_knowledge(migrated_db):
    """勾选的记忆与知识库真的落地：记忆进 rough/detail 两层，知识库进 AI 自己的文件空间。"""
    import shutil

    from sqlalchemy import select

    from app.database import async_session
    from app.models.agent import Agent
    from app.models.agent_knowledge import AgentKnowledge
    from app.models.memory import DetailMemory, RoughMemory
    from app.paths import agent_dir
    from app.services.chat_import.apply_organize import apply as apply_organized

    shutil.rmtree(agent_dir(AGENT_ID), ignore_errors=True)

    async with async_session() as db:
        await _seed(db)
        agent = (await db.execute(select(Agent).where(Agent.id == AGENT_ID))).scalars().one()

        result = await apply_organized(db, agent=agent, organized={
            "memories": [{"content": "用户住在杭州，怕冷", "importance": 4}],
            "knowledge": [{"title": "世界观", "summary": "设定", "content": "# 世界观\n正文"}],
        })
        await db.commit()

        assert result == {"memories": 1, "knowledge": 1}

        rough = (await db.execute(select(RoughMemory))).scalars().all()
        assert len(rough) == 1
        assert rough[0].owner_id == AGENT_ID and rough[0].value_score == 4
        assert (await db.execute(select(DetailMemory))).scalars().all()

        docs = (await db.execute(select(AgentKnowledge))).scalars().all()
        assert len(docs) == 1 and docs[0].title == "世界观"
        assert (agent_dir(AGENT_ID) / "knowledge").exists()
        shutil.rmtree(agent_dir(AGENT_ID), ignore_errors=True)


async def test_import_twice_appends(migrated_db):
    """同一份对话导入两次就是两段历史——不去重，也不覆盖（用户可能真的聊了两遍）。"""
    from app.database import async_session
    from app.models.agent import Agent
    from app.models.chat_import import ChatImport
    from app.models.dm import DMMessage
    from app.services.chat_import import parsers
    from app.services.chat_import.importer import import_transcript

    async with async_session() as db:
        await _seed(db)
        agent = (await db.execute(select(Agent).where(Agent.id == AGENT_ID))).scalars().one()
        transcript = parsers.parse(json.dumps(SHARE))

        await import_transcript(db, agent=agent, creator_user_id=OWNER, transcript=transcript)
        await import_transcript(db, agent=agent, creator_user_id=OWNER, transcript=transcript)
        await db.commit()

        assert len((await db.execute(select(DMMessage))).scalars().all()) == 6
        assert len((await db.execute(select(ChatImport))).scalars().all()) == 2
