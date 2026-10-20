"""世界帧 —— 进群视界 = 激活一条世界专属状态帧（沉浸式上下文）

一个世界一条帧；绑定时预建一条沉睡帧（不改他现在在哪），进门时激活它。
帧激活期间工具只有「基础集 + 这个世界颁布的技能」——见 services/world/world_frame_service.py。
设计：docs/group_world/design/world_agent_capabilities.md，帧规则：docs/dev/frame_lifecycle.md。
"""
import json

import pytest
from sqlalchemy import text

from app.tools.chat_social.enter_world import EnterWorld

pytestmark = pytest.mark.anyio

WORLD_ID, GROUP_OK = 2, 59
AI_ID, AI_USER_ID = 24, 40
SKILL = "roll"


def _write_skill(name: str, types: list[str] | None = None):
    """这个世界的住民技能：manifest + code（world_frame_tools 从磁盘读，工具名即 skill 名）"""
    from app.paths import world_dir

    manifest = {
        "name": name,
        "description": f"测试技能 {name}",
        "arguments": {"type": "object", "properties": {}},
    }
    if types:
        manifest["types"] = types
    d = world_dir(WORLD_ID) / "skills" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    (d / "code.py").write_text(
        "async def run(args, ctx):\n    return {'ok': True}\n", encoding="utf-8")


def _write_world_skill():
    """通用技能（不给 types = 所有类型都可用的那个）"""
    _write_skill(SKILL)


async def _seed(db):
    from db_reset import clear

    await clear(db, "group_members", "groups", "agents", "world_bindings", "worlds", "users")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES "
        "(1, '主人', 'x', 'human'), (40, '甲AI', 'x', 'ai')"))
    await db.execute(text(
        "INSERT INTO agents (id, owner_id, name, user_id, discoverable, state_stack) VALUES "
        "(24, 1, '甲AI', 40, true, '[]'::jsonb)"))
    await db.execute(text(
        "INSERT INTO groups (id, name, owner_type, owner_id, avatar_mode, include_ai_in_avatar, "
        "searchable, auto_approve_join, approve_invites) VALUES "
        "(59, '甲群', 'human', 1, 'default', true, true, true, true)"))
    await db.execute(text(
        "INSERT INTO group_members (group_id, member_type, member_id, role) VALUES "
        "(59, 'ai', 40, 'member')"))
    await db.execute(text("INSERT INTO worlds (id, name, owner_id) VALUES (2, '测试世界', 1)"))
    await db.execute(text(
        "INSERT INTO world_bindings (world_id, entity_type, entity_id, group_type_slug) VALUES "
        "(2, 'group', 59, NULL)"))
    await db.commit()
    _write_world_skill()


async def _world(db):
    from app.models.world import World
    return await db.get(World, WORLD_ID)


async def _world_frames(db):
    from app.services.agent.state_stack_service import get_frames

    return [f for f in await get_frames(db, AI_ID) if f.get("context_ref") == f"world:{WORLD_ID}"]


async def test_world_tools_are_the_base_set_plus_that_worlds_skills():
    """白名单 = 基础集 ∪ 这个世界当前颁布的技能（工具名同一份来源）"""
    from app.services.world.world_frame_service import WORLD_FRAME_BASE_TOOLS, world_frame_tools

    _write_world_skill()
    assert set(WORLD_FRAME_BASE_TOOLS) <= set(world_frame_tools(WORLD_ID))
    assert SKILL in world_frame_tools(WORLD_ID)
    # 群聊社交那套不该出现在世界里
    for platform_tool in ("send_message", "read_conversation", "search_groups"):
        assert platform_tool not in world_frame_tools(WORLD_ID)


async def test_binding_parks_a_dormant_frame_and_keeps_him_where_he_is(migrated_db):
    """绑定时预建的是**沉睡**帧：在栈里、已结束、不改变当前帧"""
    from app.database import async_session
    from app.services.agent.state_stack_service import (
        ensure_active_frame, get_current_frame,
    )
    from app.services.world.world_frame_service import park_world_frame_for_binding

    async with async_session() as db:
        await _seed(db)
        await ensure_active_frame(db, AI_ID, "group_chat", "group:59", "甲群", "某人")
        await db.commit()
        before = await get_current_frame(db, AI_ID)
        assert before["context_ref"] == "group:59", before

        created = await park_world_frame_for_binding(db, AI_USER_ID, await _world(db))
        await db.commit()
        assert created is True

        frames = await _world_frames(db)
        assert len(frames) == 1 and frames[0]["status"] == "ended", frames
        after = await get_current_frame(db, AI_ID)
        assert after["id"] == before["id"], "预建世界帧不该把他从群会话里拽走"

        # 幂等：再绑一次（或已预建）不叠第二帧
        assert await park_world_frame_for_binding(db, AI_USER_ID, await _world(db)) is False
        assert len(await _world_frames(db)) == 1


async def test_binding_a_world_to_an_ai_parks_its_frame(migrated_db):
    """绑定的那条路也要预建（不是只有 park_* 函数被调用时才算）"""
    from app.database import async_session
    from app.repositories.world_repo import SQLAlchemyWorldRepository
    from app.services.world.world_service import bind_entity

    async with async_session() as db:
        await _seed(db)
        await bind_entity(SQLAlchemyWorldRepository(db), WORLD_ID, 1, "agent", AI_USER_ID)
        await db.commit()

        frames = await _world_frames(db)
        assert len(frames) == 1 and frames[0]["status"] == "ended", frames
        assert frames[0]["doing"].startswith("待进入世界「测试世界」"), frames[0]


async def test_enter_world_wakes_the_parked_frame(migrated_db):
    """进门激活的就是预建的那条帧（帧身份不变），并带上"从哪来"的交接包"""
    from app.database import async_session
    from app.services.agent.state_stack_service import ensure_active_frame
    from app.services.world.world_frame_service import park_world_frame

    async with async_session() as db:
        await _seed(db)
        # 先在私信里说话：进世界是从那段会话切过来的，交接包要记着它
        await ensure_active_frame(db, AI_ID, "dm", "41_999", "若梦", "若梦")
        await db.commit()
        assert await park_world_frame(db, AI_ID, await _world(db)) is True
        await db.commit()
        parked_id = (await _world_frames(db))[0]["id"]

        out = await EnterWorld().execute(db, AI_ID, GROUP_OK, {}, {})
        assert out.get("success") and out["restored"] is True, out

        frames = await _world_frames(db)
        assert len(frames) == 1 and frames[0]["id"] == parked_id, frames
        assert frames[0]["status"] == "active" and frames[0]["group_id"] == GROUP_OK, frames[0]
        handoff = frames[0].get("handoff") or {}
        assert handoff.get("from_context_ref") == "41_999", handoff
        assert "若梦" in (handoff.get("from_label") or ""), handoff
        handoff = frames[0].get("handoff") or {}
        assert handoff.get("from_context_ref") == "41_999", handoff
        assert "若梦" in (handoff.get("from_label") or ""), handoff


async def test_inside_the_world_only_world_tools_are_allowed(migrated_db):
    """帧激活期间的工具闸：基础集 + 世界侧技能，其余平台工具不出现"""
    from app.database import async_session
    from app.services.agent.state_stack_service import get_active_frame_tools
    from app.services.world.world_frame_service import (
        WORLD_FRAME_BASE_TOOLS, active_world,
    )

    async with async_session() as db:
        await _seed(db)
        assert await active_world(db, AI_ID) == (None, None)
        assert await get_active_frame_tools(db, AI_ID) == (None, None), "普通会话帧不隔离工具"

        await EnterWorld().execute(db, AI_ID, GROUP_OK, {}, {})
        allowed, skills = await get_active_frame_tools(db, AI_ID)
        assert skills is None
        assert set(WORLD_FRAME_BASE_TOOLS) <= set(allowed)
        assert SKILL in allowed
        assert "send_message" not in allowed
        assert await active_world(db, AI_ID) == (WORLD_ID, GROUP_OK)


async def test_a_typed_skill_is_only_allowed_for_that_type(migrated_db):
    """分层注入同一口径：带 types 的技能只给那些类型的 AI（白名单不比实际注入宽）"""
    from app.database import async_session
    from app.models.agent import Agent
    from app.services.world.world_frame_service import world_frame_tools, world_frame_tools_for

    _write_skill("strike", types=["mechanic"])
    async with async_session() as db:
        await _seed(db)
        agent = await db.get(Agent, AI_ID)
        # 不区分类型时只算通用技能
        assert "strike" not in world_frame_tools(WORLD_ID)
        assert "strike" not in await world_frame_tools_for(db, agent, WORLD_ID)
        # 群里绑了 mechanic 类型：从这个群进世界（通道群=59）才拿到它
        await db.execute(text(
            "UPDATE world_bindings SET group_type_slug='mechanic' "
            "WHERE entity_type='group' AND entity_id=59"))
        await db.commit()
        assert "strike" in await world_frame_tools_for(db, agent, WORLD_ID, GROUP_OK)
        assert "strike" not in await world_frame_tools_for(db, agent, WORLD_ID)


async def test_the_group_talk_does_not_drag_him_out_of_the_world(migrated_db):
    """在世界里时，群里来消息不会把身份切回群会话——沉浸靠这一条"""
    from app.database import async_session
    from app.services.agent.state_stack_service import ensure_active_frame, get_current_frame
    from app.utils.pure.state_stack import world_id_of

    async with async_session() as db:
        await _seed(db)
        await EnterWorld().execute(db, AI_ID, GROUP_OK, {}, {})
        await db.commit()

        await ensure_active_frame(db, AI_ID, "group_chat", "group:59", "甲群", "某人")
        await db.commit()
        current = await get_current_frame(db, AI_ID)
        assert world_id_of(current) == WORLD_ID, current
        assert current["doing"].startswith(f"在世界「测试世界」里游玩"), current["doing"]
