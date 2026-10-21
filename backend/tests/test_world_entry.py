"""AI 进世界 / 世界命令：帧直连世界，不借群聊

一个世界一条帧（type=world、context_ref=world:{id}）：世界绑几个群都是这一条，
帧上记的是**入场身份**（world_role），不是通道群。
world_command 也不再"发一条群消息"，而是把事件直接喂给世界程序（world_event_hook.deliver_command），
非常驻世界同步拿回 handle() 的返回值——这就是世界的回话。

设计见 docs/group_world/design/world_agent_capabilities.md，帧规则见 docs/dev/frame_lifecycle.md。
"""
import contextlib

import pytest
from sqlalchemy import text

# 工具类可以放顶层（app.tools.base 只依赖 sqlalchemy）；async_session 不行——app.database 在
# import 时就建 engine（读 DATABASE_URL），必须等 conftest 把环境变量指向测试库之后再导入。
from app.tools.chat_social.enter_world import EnterWorld
from app.tools.chat_social.world_command import WorldCommand

pytestmark = pytest.mark.anyio

WORLD_ID = 2
GROUP_OK, GROUP_ALSO_OK, GROUP_FREE = 59, 58, 71     # 前两个绑同一个世界，第三个没绑
AI_ID, AI_USER_ID = 24, 40


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
        "(59, '甲群', 'human', 1, 'default', true, true, true, true), "
        "(58, '乙群', 'human', 1, 'default', true, true, true, true), "
        "(71, '没绑世界的群', 'human', 1, 'default', true, true, true, true)"))
    await db.execute(text(
        "INSERT INTO group_members (group_id, member_type, member_id, role) VALUES "
        "(59, 'ai', 40, 'member'), (58, 'ai', 40, 'member'), (71, 'ai', 40, 'member')"))
    await db.execute(text("INSERT INTO worlds (id, name, owner_id) VALUES (2, '测试世界', 1)"))
    await db.execute(text(
        "INSERT INTO world_bindings (world_id, entity_type, entity_id, group_type_slug) VALUES "
        "(2, 'group', 59, NULL), (2, 'group', 58, NULL)"))
    await db.commit()


@contextlib.asynccontextmanager
async def _stub_world_delivery(record: list, result=None):
    """把"喂给世界程序"这一步换成只记账的假的：这里测的是有没有绕道群聊、事件长什么样。"""
    from app.services.world import world_event_hook

    original = world_event_hook.dispatch_event

    async def fake(db, world, event):
        record.append({"world_id": world.id, "event": event})
        return {"mode": "trigger", "success": True, "result": result}

    world_event_hook.dispatch_event = fake
    try:
        yield record
    finally:
        world_event_hook.dispatch_event = original


@contextlib.asynccontextmanager
async def _stub_send_gm(record: list):
    """群消息出口换成假的：命令不该再走这条路。"""
    import app.chat.gm as gm

    original = gm.send_gm_message

    async def fake(db, group_id, sender_type, sender_id, content, **kwargs):
        record.append({"group_id": group_id, "content": content})
        return None

    gm.send_gm_message = fake
    try:
        yield record
    finally:
        gm.send_gm_message = original


async def _world_frame(db):
    from app.services.agent.state_stack_service import get_frames
    from app.utils.pure.state_stack import context_frames
    return context_frames(await get_frames(db, AI_ID), f"world:{WORLD_ID}")


async def test_command_reaches_the_world_program_without_a_group(migrated_db):
    """在世界里发命令：事件直达世界程序，一件群消息都不发，返回值就是世界的回话。"""
    from app.database import async_session

    seen: list = []
    sent: list = []
    async with _stub_world_delivery(seen, result={"ok": True, "handled": "checkin"}), \
            _stub_send_gm(sent), async_session() as db:
        await _seed(db)
        assert (await EnterWorld().execute(db, AI_ID, GROUP_OK, {}, {})).get("success")
        await db.commit()
        out = await WorldCommand().execute(db, AI_ID, None, {"command": "签到"}, {})

    assert out.get("success") and out["world_result"] == {"ok": True, "handled": "checkin"}, out
    assert sent == [], f"命令不该再借群聊投递：{sent}"
    assert seen and seen[-1]["world_id"] == WORLD_ID, seen
    event = seen[-1]["event"]
    assert event["source"] == "ai" and event["group_id"] is None, event
    assert event["messages"][0]["content"] == "签到", event


async def test_command_outside_the_world_tells_him_to_enter_first(migrated_db):
    """不在世界里：不猜世界、也不发群消息，直接说先 enter_world。"""
    from app.database import async_session

    seen: list = []
    async with _stub_world_delivery(seen), async_session() as db:
        await _seed(db)
        out = await WorldCommand().execute(db, AI_ID, GROUP_OK, {"command": "签到"}, {})

    assert out.get("error") and "enter_world" in out["message"], out
    assert seen == [], seen


async def test_enter_world_does_not_need_a_group(migrated_db):
    """世界直接绑了 AI（agent 绑定）时，手上一个群都没有也能进世界。"""
    from app.database import async_session

    async with async_session() as db:
        await _seed(db)
        await db.execute(text(
            "INSERT INTO world_bindings (world_id, entity_type, entity_id, group_type_slug) VALUES "
            f"({WORLD_ID}, 'agent', {AI_USER_ID}, 'mechanic')"))
        await db.commit()

        out = await EnterWorld().execute(db, AI_ID, None, {}, {})
        assert out.get("success") and out.get("role") == "mechanic", out

        live = await _world_frame(db)
        assert len(live) == 1, live
        assert "group_id" not in live[0], f"帧不该再记通道群：{list(live[0].keys())}"
        assert live[0]["world_role"] == "mechanic", live[0]


async def test_enter_world_from_dm_when_a_group_he_is_in_binds_it(migrated_db):
    """私信里也进得去：世界绑的是群，只要他在那个群里，就与他此刻在哪个会话无关。"""
    from app.database import async_session

    async with async_session() as db:
        await _seed(db)
        out = await EnterWorld().execute(db, AI_ID, None, {}, {})
        assert out.get("success") and out["world_id"] == WORLD_ID, out
        assert len(await _world_frame(db)) == 1


async def test_one_world_has_one_frame_no_matter_which_group_he_enters_from(migrated_db):
    """一个世界绑多个群：从哪个群进都是同一条帧，帧上也没有群可刷。"""
    from app.database import async_session

    async with async_session() as db:
        await _seed(db)
        first = await EnterWorld().execute(db, AI_ID, GROUP_OK, {}, {})
        assert first.get("success") and first["restored"] is False, first
        frame_id = (await _world_frame(db))[0]["id"]

        second = await EnterWorld().execute(db, AI_ID, GROUP_ALSO_OK, {}, {})
        assert second.get("success") and second["restored"] is True, second

        live = await _world_frame(db)
        assert len(live) == 1 and live[0]["id"] == frame_id, live
        assert "group_id" not in live[0], live[0]


async def test_the_role_is_set_at_the_door_and_kept_afterwards(migrated_db):
    """身份在进门那一刻定（从哪个群进=落到那个群的居民类型）；换门不把身份抹掉。"""
    from app.database import async_session

    async with async_session() as db:
        await _seed(db)
        await db.execute(text(
            "UPDATE world_bindings SET group_type_slug='mechanic' "
            f"WHERE entity_type='group' AND entity_id={GROUP_OK}"))
        await db.commit()

        typed = await EnterWorld().execute(db, AI_ID, GROUP_OK, {}, {})
        assert typed.get("success") and typed["role"] == "mechanic", typed
        assert (await _world_frame(db))[0]["world_role"] == "mechanic"

        # 另一个群没标类型：帧上的身份沿用，不因为换了门就变成"无身份"
        again = await EnterWorld().execute(db, AI_ID, GROUP_ALSO_OK, {}, {})
        assert again.get("success") and again["role"] is None, again
        assert (await _world_frame(db))[0]["world_role"] == "mechanic"


async def test_enter_world_refuses_a_world_that_is_not_bound_to_him(migrated_db):
    """明确点了一个既没绑他、也没绑他所在群的世界：如实拒。"""
    from app.database import async_session

    async with async_session() as db:
        await _seed(db)
        await db.execute(text("INSERT INTO worlds (id, name, owner_id) VALUES (3, '别人世界', 1)"))
        await db.commit()

        out = await EnterWorld().execute(db, AI_ID, GROUP_OK, {"world_id": 3}, {})
        assert out.get("error") and "既没绑定你" in out["message"], out
        assert await _world_frame(db) == []


async def test_enter_world_asks_which_one_when_several_are_reachable(migrated_db):
    """手上可进的世界不止一个：不替他挑，让他用 world_id 指定。"""
    from app.database import async_session

    async with async_session() as db:
        await _seed(db)
        await db.execute(text("INSERT INTO worlds (id, name, owner_id) VALUES (3, '另一个世界', 1)"))
        await db.execute(text(
            "INSERT INTO world_bindings (world_id, entity_type, entity_id, group_type_slug) VALUES "
            f"(3, 'group', {GROUP_OK}, NULL)"))
        await db.commit()

        out = await EnterWorld().execute(db, AI_ID, GROUP_OK, {}, {})
        assert out.get("error") and "world_id" in out["message"], out
        assert await _world_frame(db) == []
