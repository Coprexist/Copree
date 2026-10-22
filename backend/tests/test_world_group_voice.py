"""世界在群里说话：发言身份是群助手，不是群主也不是普通成员。

一个群助手 = group_assistants 里一行，消息身份是 (sender_type="assistant", sender_id=ga.id)——
sender_type 说明 id 属于哪张表，和 human/ai 指向 users 同一套读法，不靠正负号。
世界程序（/group/messages 受控 API）、世界 AI（send_group_message 工具）、世界 skill
（ctx.group.send）三条路都走 group_assistant_service.say_in_group，同一个身份。

为什么值得单独立规矩：借群主身份发消息不只是"看着像群主在说"——群历史里它就是群主说的话，
读这段历史的 AI、导出、记忆检索全都会当成那个人在说，等于世界假扮了他。

设计见 docs/group_world/design/world_skill_design.md，身份约定见 models/world.py GroupAssistant。
"""
import contextlib
import json

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.anyio

WORLD_ID, OWNER, GROUP_A, GROUP_B = 2, 1, 59, 58


async def _seed(db, *, bindings=((GROUP_A, None),), assistants=()):
    """一个世界、两个群（A 必绑，B 可选）、可选的既有群助手（id 显式写死便于断言身份）"""
    from db_reset import clear

    await clear(db, "groups", "agents", "worlds", "users")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES (1, '主人', 'x', 'human')"))
    await db.execute(text(
        "INSERT INTO groups (id, name, owner_type, owner_id, avatar_mode, include_ai_in_avatar, "
        "searchable, auto_approve_join, approve_invites) VALUES "
        "(59, '远征队群', 'human', 1, 'default', true, true, true, true), "
        "(58, '公会群', 'human', 1, 'default', true, true, true, true)"))
    await db.execute(text("INSERT INTO worlds (id, name, owner_id) VALUES (2, '测试世界', 1)"))
    for gid, slug in bindings:
        await db.execute(text(
            "INSERT INTO world_bindings (world_id, entity_type, entity_id, group_type_slug) "
            "VALUES (2, 'group', :gid, :slug)"), {"gid": gid, "slug": slug})
    for ga_id, gid, slug, name in assistants:
        await db.execute(text(
            "INSERT INTO group_assistants (id, group_id, world_id, group_type_slug, name) "
            "VALUES (:id, :gid, 2, :slug, :name)"),
            {"id": ga_id, "gid": gid, "slug": slug, "name": name})
    await db.commit()


@contextlib.asynccontextmanager
async def _spy_broadcast(record: list):
    """盯实时推送：群里的人当场看到的必须是助手名，不是「ai:-7」"""
    from app.chat import group_delivery

    original = group_delivery.broadcast_group_message

    async def fake(group_id, msg_data):
        record.append({"group_id": group_id, "data": msg_data})

    group_delivery.broadcast_group_message = fake
    try:
        yield record
    finally:
        group_delivery.broadcast_group_message = original


async def _world(db):
    from app.models.world import World

    return await db.get(World, WORLD_ID)


async def test_world_speaks_as_the_group_assistant(migrated_db):
    """世界发群消息：身份是本群群助手（assistant / ga.id），不是群主（human / 1）。"""
    from app.database import async_session
    from app.repositories.world_repo import SQLAlchemyWorldRepository
    from app.tools.world import run_world_tool

    sent: list = []
    async with _spy_broadcast(sent), async_session() as db:
        await _seed(db, assistants=[(7, GROUP_A, "expedition", "残响记录员")])
        out = await run_world_tool(SQLAlchemyWorldRepository(db), await _world(db),
                                   "send_group_message",
                                   json.dumps({"content": "欢迎来到这个世界"}))
        assert out.get("success"), out
        rows = (await db.execute(text(
            "SELECT sender_type, sender_id, content FROM messages WHERE group_id = :g ORDER BY id"
        ), {"g": GROUP_A})).all()

    assert [(r[0], r[1]) for r in rows] == [("assistant", 7)], f"世界的话记到了别人名下：{rows}"
    assert rows[0][2] == "欢迎来到这个世界"
    assert sent and sent[-1]["group_id"] == GROUP_A
    assert sent[-1]["data"]["sender_id"] == 7
    assert sent[-1]["data"]["sender_name"] == "残响记录员", sent[-1]["data"]


async def test_a_group_without_an_assistant_gets_one_named_after_the_world(migrated_db):
    """历史绑定没有类型、也就没有助手：第一次发言时补建一个（用世界的名字），之后一直用它。"""
    from app.database import async_session
    from app.repositories.world_repo import SQLAlchemyWorldRepository
    from app.tools.world import run_world_tool

    async with async_session() as db:
        await _seed(db)                       # 只绑群，没有类型、没有助手
        repo, world = SQLAlchemyWorldRepository(db), await _world(db)
        for content in ("第一条", "第二条"):
            out = await run_world_tool(repo, world, "send_group_message", json.dumps({"content": content}))
            assert out.get("success"), out
        assistants = (await db.execute(text(
            "SELECT id, name, group_type_slug FROM group_assistants WHERE group_id = :g"
        ), {"g": GROUP_A})).all()
        senders = (await db.execute(text(
            "SELECT sender_id FROM messages WHERE group_id = :g ORDER BY id"
        ), {"g": GROUP_A})).scalars().all()

    assert len(assistants) == 1, f"补建不是幂等的：{assistants}"
    ga_id, ga_name, ga_slug = assistants[0]
    assert ga_name == "测试世界" and ga_slug is None, assistants
    assert list(senders) == [ga_id, ga_id], f"两次发言该是同一个助手：{senders}"


async def test_each_group_gets_its_own_assistant(migrated_db):
    """世界绑两个群：A 群的话记在 A 的助手名下，不会串到 B 群已存在的助手头上。"""
    from app.database import async_session
    from app.repositories.world_repo import SQLAlchemyWorldRepository
    from app.tools.world import run_world_tool

    async with async_session() as db:
        await _seed(db, bindings=[(GROUP_A, None), (GROUP_B, "guild")],
                    assistants=[(3, GROUP_B, "guild", "公会接待员")])
        repo, world = SQLAlchemyWorldRepository(db), await _world(db)
        assert (await run_world_tool(repo, world, "send_group_message",
                                     json.dumps({"content": "来公会", "group_id": GROUP_B}))).get("success")
        assert (await run_world_tool(repo, world, "send_group_message",
                                     json.dumps({"content": "来远征"}))).get("success")
        by_group = dict((await db.execute(text(
            "SELECT group_id, sender_id FROM messages"
        ))).all())
        new_ga = (await db.execute(text(
            "SELECT id, name FROM group_assistants WHERE group_id = :g"
        ), {"g": GROUP_A})).all()

    assert by_group[GROUP_B] == 3, by_group
    assert len(new_ga) == 1 and new_ga[0][1] == "测试世界", new_ga
    assert by_group[GROUP_A] == new_ga[0][0], f"A 群该用 A 群自己的助手：{by_group}"


async def test_sender_names_reads_assistants_by_type(migrated_db):
    """群助手 id 与用户 id 同号也不会认错：发送者按 (sender_type, sender_id) 查名字。"""
    from app.database import async_session
    from app.utils.display_name import sender_names

    async with async_session() as db:
        await _seed(db, assistants=[(1, GROUP_A, "expedition", "残响记录员")])
        # 助手 id 故意与人（users.id=1「主人」）同号：只看 id 就会把他俩认成同一个人
        names = await sender_names(db, {("human", OWNER), ("assistant", 1), ("assistant", 999)})

    assert names[("human", OWNER)] == "主人"
    assert names[("assistant", 1)] == "残响记录员"
    assert names[("assistant", 999)] == "用户999", names


async def test_skill_group_send_speaks_as_the_assistant(migrated_db):
    """世界 skill 的 ctx.group.send 与工具同一个身份（这条路径原先连参数都是错的，
    传的是 human/owner 且重复给了 sender_type——修好后必须留下证据）。"""
    from app.database import async_session
    from app.services.world import skill_sandbox

    async with async_session() as db:
        await _seed(db, assistants=[(7, GROUP_A, "expedition", "残响记录员")])
        out = await skill_sandbox._handle_call(
            db, await _world(db), {"group:send"},
            {"op": "group.send", "content": "skill 发的消息"},
        )
        assert out.get("ok"), out
        row = (await db.execute(text(
            "SELECT sender_type, sender_id, content FROM messages WHERE id = :i"
        ), {"i": out["data"]["message_id"]})).first()

    assert row is not None and (row[0], row[1]) == ("assistant", 7), row
    assert row[2] == "skill 发的消息"
