"""触发组合规则契约：条件求值与扩展、校验、工具事件下的投递状态（挂在状态帧上）。"""
import pytest

pytestmark = pytest.mark.anyio


async def _seed(db):
    from sqlalchemy import text

    from db_reset import clear

    await clear(db, "agent_skills", "group_members", "groups", "messages", "agents", "users")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES "
        "(1, '主人', 'x', 'human'), (2, 'AI账号', 'x', 'ai')"))
    await db.execute(text(
        "INSERT INTO agents (id, owner_id, name, user_id, discoverable) "
        "VALUES (24, 1, '值班员', 2, true)"))
    await db.commit()


def test_conditions_compose_and_extend():
    """条件能组合（与或非），也能自己手搓运算与判词"""
    from app.utils.pure.conditions import match_conditions, register_op, register_predicate

    ctx = {"tool": "web_search", "first": True, "tool_calls": 1}
    assert match_conditions({"and": [{"tool": "web_search"}, {"first": True}]}, ctx)
    assert not match_conditions({"and": [{"tool": "web_search"}, {"first": False}]}, ctx)
    assert match_conditions({"not": {"tool": "web_fetch"}}, ctx)
    assert match_conditions({"tool_calls_lte": 3}, ctx)

    register_op("_in", lambda value, expect: value in expect)
    assert match_conditions({"tool_in": ["web_search", "web_fetch"]}, ctx)
    register_predicate("is_first", lambda c, expect: bool(c.get("first")) == bool(expect))
    assert match_conditions({"$is_first": True}, ctx)
    # 自己手搓的判词抛异常只算不命中，不炸调用方
    register_predicate("boom", lambda c, e: 1 / 0)
    assert match_conditions({"$boom": 1}, ctx) is False


def test_validate_rejects_writes_that_would_fail_silently():
    """写错的规则当场拒绝并说清可选值，不留给运行期安静地永不触发"""
    from app.utils.pure.trigger_rules import validate_trigger

    assert validate_trigger({"when": {"event": "tool_result"}, "do": {"action": "nope"}})[0] is False
    ok, err = validate_trigger({"when": {"event": "tool_result"}, "do": {"action": "deliver"}})
    assert not ok and "text" in err
    ok, err = validate_trigger({"when": {"event": "tool_result"}, "do": {"action": "silent"}, "once": "agent"})
    assert not ok and "once" in err
    assert validate_trigger({"when": {"event": "tool_result"}, "do": {"action": "silent"}})[0] is True


def test_targets_of_lets_untouched_tools_skip_state():
    """没规则盯着的工具调用零开销：连状态都不读"""
    from app.utils.pure.trigger_rules import targets_of

    rule = {"when": {"event": "tool_result",
                     "conditions": {"and": [{"tool": "web_search"}, {"first": True}]}},
            "do": {"action": "silent"}}
    assert targets_of(rule) == {"web_search"}
    assert targets_of({"when": {"event": "tool_result"}, "do": {"action": "silent"}}) is None


async def test_deliver_once_per_context_and_custom_action(migrated_db):
    """首次投一次、同会话不再投；换会话（帧重建）再投；自定义动作照跑"""
    from app.database import async_session
    from app.services.agent.state_stack_service import ensure_active_frame
    from app.services.trigger import trigger_service
    from app.utils.pure import trigger_rules

    trigger_rules.register_plugin([{
        "id": "test.deliver_once",
        "when": {"event": "tool_result", "conditions": {"and": [{"tool": "fake_tool"}, {"first": True}]}},
        "do": {"action": "deliver", "text": "先回复，再核实"},
        "once": "context",
    }])
    seen: list[int] = []
    trigger_rules.register_action(
        "count_it", lambda ctx, do, result: (seen.append(ctx["tool_calls"]), {"counted": True})[1])
    trigger_rules.register_plugin([{
        "id": "test.custom_action",
        "when": {"event": "tool_result", "conditions": {"tool": "fake_tool"}},
        "do": {"action": "count_it"},
        "once": "never",
    }])

    async with async_session() as db:
        await _seed(db)
        await ensure_active_frame(db, 24, "group_chat", "group:7", "测试群", "小明")
        await db.commit()

        first = await trigger_service.after_tool_result(db, 24, "fake_tool", {"success": True})
        assert first.get("notice") == "先回复，再核实", first
        assert first.get("counted") is True, first

        second = await trigger_service.after_tool_result(db, 24, "fake_tool", {"success": True})
        assert "notice" not in second, second
        assert len(seen) == 2, seen          # once=never 的动作每次都跑

        await ensure_active_frame(db, 24, "dm", "dm:1", "私信", "主人")
        await db.commit()
        third = await trigger_service.after_tool_result(db, 24, "fake_tool", {"success": True})
        assert third.get("notice") == "先回复，再核实", third

        plain = await trigger_service.after_tool_result(db, 24, "no_rules_tool", {"success": True})
        assert plain == {"success": True}, plain
