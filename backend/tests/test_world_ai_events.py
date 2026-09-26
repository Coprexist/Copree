"""世界事件 → AI：契约校验、收件人解析、投递（程序处理 vs 唤醒）。

契约与语义见 docs/group_world/design/world_ai_events.md。
"""
import pytest

pytestmark = pytest.mark.anyio

WORLD_ID = 2
GROUP_A, GROUP_B = 7, 8          # A 有类型 study，B 无类型
AI_A, AI_B = 24, 25              # A 在群 A（类型 knight），B 在群 B


async def _seed(db):
    from sqlalchemy import text

    from db_reset import clear

    await clear(db, "world_agents", "world_bindings", "agent_skills", "group_members",
                "groups", "agents", "worlds", "users", "messages")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES "
        "(1, '主人', 'x', 'human'), (40, '甲AI', 'x', 'ai'), (41, '乙AI', 'x', 'ai')"))
    await db.execute(text(
        "INSERT INTO agents (id, owner_id, name, user_id, discoverable) VALUES "
        "(24, 1, '甲AI', 40, true), (25, 1, '乙AI', 41, true)"))
    await db.execute(text(
        "INSERT INTO groups (id, name, owner_type, owner_id, avatar_mode, include_ai_in_avatar, "
        "searchable, auto_approve_join, approve_invites) VALUES "
        "(7, '研学群', 'human', 1, 'default', true, true, true, true), "
        "(8, '闲群', 'human', 1, 'default', true, true, true, true)"))
    await db.execute(text(
        "INSERT INTO group_members (group_id, member_type, member_id, role) VALUES "
        "(7, 'ai', 40, 'member'), (8, 'ai', 41, 'member')"))
    await db.execute(text("INSERT INTO worlds (id, name, owner_id) VALUES (2, '测试世界', 1)"))
    await db.execute(text(
        "INSERT INTO world_bindings (world_id, entity_type, entity_id, group_type_slug) VALUES "
        "(2, 'group', 7, 'study'), (2, 'group', 8, NULL)"))
    await db.execute(text(
        "INSERT INTO world_agents (world_id, agent_id, role, group_type_slug) VALUES "
        "(2, 24, 'assistant', 'knight'), (2, 25, 'resident', NULL)"))
    await db.commit()


def test_event_contract_limits():
    """契约：名字形状、标题必填、payload 上限、targets 类型——不猜，一律拒绝"""
    from app.services.world.world_ai_events import validate_event

    assert validate_event({"name": "restock_done", "title": "补货完成"})[0]
    assert not validate_event({"name": "Restock", "title": "x"})[0]
    assert not validate_event({"name": "ok", "title": ""})[0]
    assert not validate_event({"name": "ok", "title": "x" * 61})[0]
    assert not validate_event({"name": "ok", "title": "x", "payload": {"a": "b" * 5000}})[0]
    assert not validate_event({"name": "ok", "title": "x", "targets": []})[0]


def test_payload_is_flattened_for_the_rule_dsl():
    """payload 展平成 payload_*：条件 DSL 只认扁平字段"""
    from app.services.world.decision_skill import build_world_event_ctx

    ctx = build_world_event_ctx(WORLD_ID, {"name": "restock_done", "title": "补货",
                                           "payload": {"item": "面包", "count": 12}})
    assert ctx["event"] == "world_event" and ctx["name"] == "restock_done"
    assert ctx["payload_item"] == "面包" and ctx["payload_count"] == 12


async def test_targets_resolve_by_id_and_type(migrated_db):
    """收件人：显式 id、群类型、AI 类型、缺省（本世界所有绑定群里的 AI）"""
    from types import SimpleNamespace

    from app.database import async_session
    from app.services.world.world_ai_events import resolve_targets

    async with async_session() as db:
        await _seed(db)
        world = SimpleNamespace(id=WORLD_ID, config={})

        default = await resolve_targets(db, world, {})
        assert {(t["agent_id"], t["group_id"]) for t in default} == {(AI_A, GROUP_A), (AI_B, GROUP_B)}, default

        by_group_type = await resolve_targets(db, world, {"groups": {"types": ["study"]}})
        assert [t["agent_id"] for t in by_group_type] == [AI_A], by_group_type

        by_ai_id = await resolve_targets(db, world, {"ais": {"ids": [AI_B]}, "group_id": GROUP_A})
        assert by_ai_id == [{"agent_id": AI_B, "group_id": GROUP_A}], by_ai_id

        only_group = await resolve_targets(db, world, {"group_id": GROUP_B})
        assert only_group == [{"agent_id": AI_B, "group_id": GROUP_B}], only_group

        by_ai_type = await resolve_targets(db, world, {"ais": {"types": ["knight"]}, "group_id": GROUP_B})
        assert by_ai_type == [{"agent_id": AI_A, "group_id": GROUP_B}], by_ai_type


async def test_emit_event_handles_by_rule_and_wakes_the_rest(migrated_db):
    """命中规则 → 程序代发（不唤醒）；没规则 → 投唤醒队列"""
    from types import SimpleNamespace

    from sqlalchemy import select

    from app.database import async_session
    from app.models.message import Message
    from app.services.world import world_ai_events as events
    from app.services.world.decision_skill import save_decision_rule

    woke: list[dict] = []
    orig = events._enqueue_wake
    events._enqueue_wake = lambda target, world, event, note="": woke.append({"target": target, "note": note})
    try:
        async with async_session() as db:
            await _seed(db)
            world = SimpleNamespace(id=WORLD_ID, config={})
            ok, err = await save_decision_rule(db, "agent", AI_A, {
                "name": "补货就喊一声",
                "when": {"event": "world_event", "conditions": {"name": "restock_done"}},
                "do": {"action": "reply_template", "reply": "面包补货了"}, "notify": False,
            })
            assert ok, err

            handled = await events.emit_event(db, world, {
                "name": "restock_done", "title": "补货完成",
                "payload": {"item": "面包"},
                "targets": {"groups": {"ids": [GROUP_A]}},
            })
            assert handled == {"ok": True, "name": "restock_done", "targets": 1, "handled": 1, "woke": 0}, handled
            rows = [(r[0], r[1]) for r in (await db.execute(
                select(Message.content, Message.sender_type).where(Message.group_id == GROUP_A))).all()]
            assert ("面包补货了", "ai") in rows, rows
            assert woke == [], woke

            woken = await events.emit_event(db, world, {
                "name": "weather_changed", "title": "下雨了",
                "targets": {"groups": {"ids": [GROUP_B]}},
            })
            assert woken["woke"] == 1 and woken["handled"] == 0, woken
            assert woke and woke[0]["target"]["agent_id"] == AI_B, woke
    finally:
        events._enqueue_wake = orig
