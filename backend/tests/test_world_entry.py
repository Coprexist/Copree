"""AI 进世界 / 世界命令落到哪个群

一个世界一个状态：`enter_world` 把 (世界, 通道群) 记成 world 帧，同一个世界再进是复活原帧。
`world_command` 的目标群依次认：显式 group_id → 候选群里第一个绑了世界的 → 世界帧的通道群；
一个都没有时如实说「你手上没有绑了世界的群」（**不报**「群没绑世界」）。
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
AI_ID = 24


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
async def _stub_send_gm(record: list):
    """把出口换成一个只记账的假的：这里测的是"命令发到哪个群"，不该真往群里发东西"""
    import app.chat.gm as gm

    original = gm.send_gm_message

    async def fake(db, group_id, sender_type, sender_id, content, **kwargs):
        record.append({"group_id": group_id, "sender_type": sender_type, "content": content})
        return None

    gm.send_gm_message = fake
    try:
        yield record
    finally:
        gm.send_gm_message = original


async def _open_group(db, ref: str):
    from app.services.agent.state_stack_service import ensure_active_frame
    await ensure_active_frame(db, AI_ID, "group_chat", ref, "甲群", "某人")
    await db.commit()


async def _world_frame(db):
    from app.services.agent.state_stack_service import get_frames
    from app.utils.pure.state_stack import context_frames
    return context_frames(await get_frames(db, AI_ID), f"world:{WORLD_ID}")


async def test_command_goes_to_the_group_he_is_in(migrated_db):
    """群聊里省 group_id：发给本群（它就是绑了世界的那个）"""
    from app.database import async_session

    async with async_session() as db:
        await _seed(db)
        sent: list = []
        async with _stub_send_gm(sent) as record:
            out = await WorldCommand().execute(db, AI_ID, GROUP_OK, {"command": "签到"}, {})
        assert out.get("success") and out.get("world_id") == WORLD_ID, out
        assert [m["group_id"] for m in record] == [GROUP_OK], record


async def test_command_from_dm_uses_the_nearest_group_frame(migrated_db):
    """私信里没 group_id：认状态栈里最近的群会话（他刚 enter_group 进去的那个）"""
    from app.database import async_session

    async with async_session() as db:
        await _seed(db)
        await _open_group(db, f"group:{GROUP_OK}")
        sent: list = []
        async with _stub_send_gm(sent) as record:
            out = await WorldCommand().execute(db, AI_ID, None, {"command": "签到"}, {})
        assert out.get("success"), out
        assert [m["group_id"] for m in record] == [GROUP_OK], record


async def test_command_falls_back_to_the_world_frame_channel(migrated_db):
    """群会话都被交接清掉时，认世界帧上记的通道群"""
    from app.database import async_session
    from app.services.agent.state_stack_service import push_state
    from app.utils.pure.state_stack import make_state_frame

    async with async_session() as db:
        await _seed(db)
        await push_state(db, AI_ID, make_state_frame(
            type_="world", context_ref=f"world:{WORLD_ID}", group_id=GROUP_OK, doing="在世界里"))
        await db.commit()

        sent: list = []
        async with _stub_send_gm(sent) as record:
            out = await WorldCommand().execute(db, AI_ID, None, {"command": "签到"}, {})
        assert out.get("success"), out
        assert [m["group_id"] for m in record] == [GROUP_OK], record


async def test_command_reports_honestly_when_no_group_has_a_world(migrated_db):
    """一个都没有：如实说"你手上没有绑了世界的群"，且什么都不发"""
    from app.database import async_session

    async with async_session() as db:
        await _seed(db)
        await _open_group(db, f"group:{GROUP_FREE}")
        sent: list = []
        async with _stub_send_gm(sent) as record:
            out = await WorldCommand().execute(db, AI_ID, None, {"command": "签到"}, {})
        assert out.get("error") and "绑了世界的群" in out["message"], out
        assert record == [], record


async def test_command_with_an_explicit_group_argument(migrated_db):
    """工具参数里带 group_id（在私信里也能用）：命令就从那个群出去"""
    from app.database import async_session

    async with async_session() as db:
        await _seed(db)
        sent: list = []
        async with _stub_send_gm(sent) as record:
            out = await WorldCommand().execute(
                db, AI_ID, None, {"command": "签到", "group_id": GROUP_OK}, {})
        assert out.get("success"), out
        assert [m["group_id"] for m in record] == [GROUP_OK], record


async def test_command_on_an_explicit_unbound_group_says_so(migrated_db):
    """显式指了没绑世界的群：就说这个群没绑（不是"你不知道去哪"）"""
    from app.database import async_session

    async with async_session() as db:
        await _seed(db)
        sent: list = []
        async with _stub_send_gm(sent) as record:
            out = await WorldCommand().execute(
                db, AI_ID, None, {"command": "签到", "group_id": GROUP_FREE}, {})
        assert out.get("error") and f"群 {GROUP_FREE} 未绑定世界" in out["message"], out
        assert record == [], record


async def test_enter_world_explicit_group_argument_wins(migrated_db):
    """工具参数里的 group_id 压过"我正在哪个群说话"：通道群按参数走"""
    from app.database import async_session

    async with async_session() as db:
        await _seed(db)
        out = await EnterWorld().execute(db, AI_ID, GROUP_OK, {"group_id": GROUP_ALSO_OK}, {})
        assert out.get("success") and out["group_id"] == GROUP_ALSO_OK, out
        live = await _world_frame(db)
        assert len(live) == 1 and live[0]["group_id"] == GROUP_ALSO_OK, live


async def test_enter_world_records_the_channel_and_reuses_the_frame(migrated_db):
    """进世界建 world 帧（记通道群）；同一个世界再进是复活原帧，不叠第二帧"""
    from app.database import async_session

    async with async_session() as db:
        await _seed(db)
        first = await EnterWorld().execute(db, AI_ID, GROUP_OK, {}, {})
        assert first.get("success") and first["group_id"] == GROUP_OK, first
        assert first.get("restored") is False
        live = await _world_frame(db)
        assert len(live) == 1 and live[0]["group_id"] == GROUP_OK, live
        frame_id = live[0]["id"]

        second = await EnterWorld().execute(db, AI_ID, GROUP_OK, {}, {})
        assert second.get("restored") is True, second
        live = await _world_frame(db)
        assert len(live) == 1 and live[0]["id"] == frame_id, live


async def test_enter_world_refreshes_the_channel_group(migrated_db):
    """一个世界绑多个群：从另一个群进，帧上的通道群跟着变（帧身份不变）"""
    from app.database import async_session

    async with async_session() as db:
        await _seed(db)
        await EnterWorld().execute(db, AI_ID, GROUP_OK, {}, {})
        frame_id = (await _world_frame(db))[0]["id"]

        out = await EnterWorld().execute(db, AI_ID, GROUP_ALSO_OK, {}, {})
        assert out.get("success") and out["group_id"] == GROUP_ALSO_OK, out
        live = await _world_frame(db)
        assert len(live) == 1 and live[0]["id"] == frame_id, live
        assert live[0]["group_id"] == GROUP_ALSO_OK, live
        assert f"群「乙群」" in live[0]["doing"], live[0]["doing"]
