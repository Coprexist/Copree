"""跨情景经历：写一行、进过就不再投、compact 之后可以再投

设计见 docs/dev/conversation_history.md §5.1 与 models/agent_experience.py：
- 经历行是「我在哪儿干过什么」的指针，不进向量记忆、不进结构记忆、不参与衰减；
- 归属是 owner_type + owner_id（主站 ai / 世界 world_ai），表与读写只有一份；
- 投递键是账本条目的 `exp:<id>`：进过这个上下文就不再投；`drop_on_unlock` 让它在
  解锁（compact）时离场，于是「compact 之后不算投过」是免费得到的；
- 本场自己产出的行不回灌本场；
- 切场景只补「刚离开那一场」的最新几行，那一场一行都没有时才用工具总账兜底。
"""
import pytest

pytestmark = pytest.mark.anyio

AGENT = 24


async def _seed(db):
    from sqlalchemy import text

    from db_reset import clear

    await clear(db, "agent_experiences", "agent_history_entries", "agents", "users")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES "
        "(1, '甲', 'x', 'human'), (2, 'AI账号', 'x', 'ai')"))
    await db.execute(text(
        "INSERT INTO agents (id, owner_id, name, user_id, discoverable) "
        "VALUES (24, 1, '化学老师', 2, true)"))


async def _add_experience(db, scene: str, summary: str, *, label: str = "", source: str = "key_note"):
    from app.models.agent_experience import OWNER_AI, AgentExperience

    row = AgentExperience(
        owner_type=OWNER_AI, owner_id=AGENT, scene_key=scene, scene_label=label or scene,
        frame_type="group_chat", source=source, summary=summary, tools="",
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def test_recall_injects_once_and_then_stays_quiet(migrated_db):
    from app.database import async_session
    from app.services.experience.experience_delivery import deliver_recalled_experiences

    async with async_session() as db:
        await _seed(db)
        row = await _add_experience(db, "group:2", "把 world_chat_service 的阈值从 300 调到 500")

        first = await deliver_recalled_experiences(
            db, owner_id=AGENT, context_ref="group:1", scene="group:1",
            limit=5, query="world_chat_service 阈值 怎么调")
        assert len(first) == 1, first
        assert first[0]["kind"] == "experience"
        assert first[0]["ref"] == f"exp:{row.id}"
        assert "【想起】" in first[0]["content"]
        assert "把 world_chat_service 的阈值" in first[0]["content"]
        assert f"exp:{row.id}" not in first[0]["content"], "ref 是服务端字段，不进请求体"

        # 同一上下文再相干也不重复投
        assert await deliver_recalled_experiences(
            db, owner_id=AGENT, context_ref="group:1", scene="group:1",
            limit=5, query="world_chat_service 阈值 怎么调") == []


async def test_own_scene_rows_are_not_injected_back(migrated_db):
    """本场产出的行本来就在上下文里，再「想起」一次是噪声"""
    from app.database import async_session
    from app.services.experience.experience_delivery import deliver_recalled_experiences

    async with async_session() as db:
        await _seed(db)
        await _add_experience(db, "group:1", "改了这个群的阈值")

        assert await deliver_recalled_experiences(
            db, owner_id=AGENT, context_ref="group:1", scene="group:1",
            limit=5, query="阈值") == []


async def test_compact_makes_rows_deliverable_again(migrated_db):
    """compact 之后这个上下文就算「没看过」：解锁时带 drop_on_unlock 的条目离场"""
    from app.database import async_session
    from app.models.agent import Agent
    from app.services.experience.experience_delivery import deliver_recalled_experiences
    from app.services.history import history_service
    from app.services.history.context_sync import rewrite_context

    async with async_session() as db:
        await _seed(db)
        await _add_experience(db, "group:2", "把阈值从 300 调到 500")
        agent = await db.get(Agent, AGENT)

        first = await deliver_recalled_experiences(
            db, owner_id=AGENT, context_ref="group:1", scene="group:1",
            limit=5, query="阈值")
        assert len(first) == 1

        # 请求真的发出去 → 打 seen；解锁重写才会让它离场（没投出去的条目要留）
        await history_service.mark_seen(db, first)
        await rewrite_context(db, agent, "group:1", summary="这一段聊了阈值", keep_last=0)
        await db.commit()

        again = await deliver_recalled_experiences(
            db, owner_id=AGENT, context_ref="group:1", scene="group:1",
            limit=5, query="阈值")
        assert len(again) == 1, "解锁后应当可以重投"


async def test_switch_delivers_only_the_scene_just_left(migrated_db):
    """频繁切换只补最新的：A 场的老行不会被带进 C，只有刚离开的 B 场进账本"""
    from app.database import async_session
    from app.services.experience.experience_delivery import deliver_switch_experiences

    async with async_session() as db:
        await _seed(db)
        await _add_experience(db, "group:1", "旧的一场：改过搜索工具")
        await _add_experience(db, "group:2", "刚离开的一场：把阈值调到 500")

        handoff = {"from_context_ref": "group:2", "from_label": "化学群",
                   "tail": ["甲: 阈值调到 500 了"]}
        entries = await deliver_switch_experiences(
            db, owner_id=AGENT, context_ref="group:3", scene="group:3",
            handoff=handoff, limit=5)
        joined = "\n".join(e["content"] for e in entries)
        assert "阈值调到 500" in joined, joined
        assert "改过搜索工具" not in joined, "只补刚离开那一场，不回溯历史场景"


async def test_switch_is_not_triggered_without_a_handoff(migrated_db):
    """不是刚切过来就不投：否则每轮都把「上一次切过来的那场」补一遍"""
    from app.database import async_session
    from app.services.experience.experience_delivery import deliver_switch_experiences

    async with async_session() as db:
        await _seed(db)
        await _add_experience(db, "group:2", "刚离开的一场：把阈值调到 500")

        assert await deliver_switch_experiences(
            db, owner_id=AGENT, context_ref="group:3", scene="group:3",
            handoff={}, limit=5) == []


async def test_switch_falls_back_to_tool_ledger(migrated_db):
    """那一场一句 key_note 都没写：用账本里的工具总账补一行（source=tool_ledger）"""
    from sqlalchemy import select

    from app.database import async_session
    from app.models.agent import Agent
    from app.models.agent_experience import AgentExperience
    from app.services.experience.experience_delivery import deliver_switch_experiences
    from app.services.history.context_sync import append_events
    from app.utils.pure.history import make_entry, tools_entry

    async with async_session() as db:
        await _seed(db)
        agent = await db.get(Agent, AGENT)
        await append_events(db, agent, "group:2", [
            tools_entry([{"name": "execute_command", "note": "ok"}]),
            make_entry("message", "甲: 好", actor="user"),
        ])

        handoff = {"from_context_ref": "group:2", "from_label": "化学群", "tail": ["甲: 好"]}
        entries = await deliver_switch_experiences(
            db, owner_id=AGENT, context_ref="group:3", scene="group:3",
            handoff=handoff, limit=5)
        assert len(entries) == 1, entries
        assert "execute_command" in entries[0]["content"]

        row = (await db.execute(select(AgentExperience))).scalars().one()
        assert row.scene_key == "group:2" and row.source == "tool_ledger"


async def test_plugin_frame_is_its_own_scene(migrated_db):
    """插件帧也是场景：在帧里写的事落在帧的 context_ref 上，切回群时投得到"""
    from app.database import async_session
    from app.services.experience.experience_delivery import deliver_switch_experiences

    async with async_session() as db:
        await _seed(db)
        await _add_experience(db, "bilibili:video-9", "看完了这条视频，结论在第 12 分钟")

        handoff = {"from_context_ref": "bilibili:video-9", "from_label": "B 站视频",
                   "tail": ["甲: 看完没"]}
        entries = await deliver_switch_experiences(
            db, owner_id=AGENT, context_ref="group:1", scene="group:1",
            handoff=handoff, limit=5)
        assert len(entries) == 1, entries
        assert "第 12 分钟" in entries[0]["content"]


async def test_entries_are_marked_drop_on_unlock():
    from app.utils.pure.experience_entry import (
        belongs_elsewhere, delivered_experiences, make_experience_entry,
    )

    entry = make_experience_entry(
        {"id": 7, "scene_key": "group:2", "scene_label": "化学群", "summary": "改了阈值"},
        lead=True,
    )
    assert entry["ref"] == "exp:7"
    assert entry["flags"] == {"drop_on_unlock": True}
    assert "别的会话" in entry["content"], "第一次投时说一次边界"
    assert delivered_experiences([entry]) == {"7"}
    assert delivered_experiences([{"ref": "mem:1@ab"}]) == set()
    assert belongs_elsewhere({"scene_key": "group:2"}, "group:1") is True
    assert belongs_elsewhere({"scene_key": "group:1"}, "group:1") is False


def test_experience_slots_never_exceed_the_combined_cap():
    from app.utils.pure.injection_budget import experience_slots

    assert experience_slots(0, memory_max=5, experience_max=5, total_max=7) == 5
    assert experience_slots(5, memory_max=5, experience_max=5, total_max=7) == 2
    assert experience_slots(9, memory_max=5, experience_max=5, total_max=7) == 2, "超出的一律按上限算"
    assert experience_slots(7, memory_max=5, experience_max=5, total_max=7) == 2, "记忆已封顶，按 5 算"


async def test_note_experience_tool_writes_then_lists_from_elsewhere(migrated_db):
    """AI 自己写的入口：写入当前场，list 在别的会话里能看见它"""
    from app.database import async_session
    from app.tools.self_management.note_experience import NoteExperience

    async with async_session() as db:
        await _seed(db)
        tool = NoteExperience()
        ctx = {"conversation_type": "group", "session_id": None}

        added = await tool.execute(db, AGENT, 66, {"action": "add", "what": "把阈值调到 500"}, ctx)
        assert added.get("success"), added

        # 同场 list 不显示自己刚写的那行（那是「别处」的索引，不是本场的流水）
        same = await tool.execute(db, AGENT, 66, {"action": "list"}, ctx)
        assert same["recent"] == []

        elsewhere = await tool.execute(
            db, AGENT, None, {"action": "list"},
            {"conversation_type": "dm", "session_id": "40_90"})
        assert [r["summary"] for r in elsewhere["recent"]] == ["把阈值调到 500"]
