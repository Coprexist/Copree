"""触发组合规则契约：条件写法与扩展、作用域、信任边界、可观测。"""
import pytest

pytestmark = pytest.mark.anyio


def _raises(fn) -> bool:
    """本仓库的测试跑在自带的极简 runner 上，没有 pytest.raises，手动判。"""
    try:
        fn()
    except ValueError:
        return True
    return False


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


def test_one_canonical_condition_shape_plus_legacy_shorthand():
    """条件只有一种规范写法；历史下划线写法仍认（库里已有的决策技能规则是那种）"""
    from app.utils.pure.conditions import match_conditions, register_op

    ctx = {"tool": "web_search", "first_in_frame": True, "calls_in_frame": 1, "ok": True}
    assert match_conditions({"field": "tool", "op": "in", "value": ["web_search", "web_fetch"]}, ctx)
    assert match_conditions({"field": "tool", "op": "contains", "value": "search"}, ctx)
    assert not match_conditions({"field": "tool", "op": "eq", "value": "web_fetch"}, ctx)
    assert match_conditions({"and": [{"field": "tool", "op": "eq", "value": "web_search"},
                                     {"not": {"field": "ok", "op": "eq", "value": False}}]}, ctx)
    assert match_conditions({"tool": "web_search"}, ctx)
    assert match_conditions({"tool_contains": "search"}, ctx)
    register_op("vendor.in_list", lambda value, expect: value in expect)
    assert match_conditions({"field": "tool", "op": "vendor.in_list", "value": ["web_search"]}, ctx)


def test_extension_names_are_namespaced_and_platform_wins():
    """扩展名必须带命名空间；平台认领过的名字插件覆盖不了"""
    from app.utils.pure import trigger_rules
    from app.utils.pure.conditions import match_conditions, register_op, register_predicate

    assert _raises(lambda: register_op("nons", lambda v, e: True))
    assert _raises(lambda: register_op("contains", lambda v, e: True))
    assert _raises(lambda: trigger_rules.register_action("nons", lambda *a: None))

    register_predicate("platform.is_admin", lambda ctx, expect: True, source="platform")
    register_predicate("platform.is_admin", lambda ctx, expect: False, source="vendor")
    assert match_conditions({"op": "$platform.is_admin", "value": True}, {}) is True


def test_scale_guards_keep_the_evaluator_pure_and_cheap():
    """嵌套与节点有上限，正则长度有上限（_matches 是唯一能写出灾难性回溯的地方）"""
    from app.utils.pure.conditions import (
        MAX_DEPTH, MAX_PATTERN, match_conditions, validate_conditions,
    )

    deep = {"not": {}}
    node = deep
    for _ in range(MAX_DEPTH + 2):
        node["not"] = {"not": {}}
        node = node["not"]
    assert match_conditions(deep, {}) is False
    assert validate_conditions({"field": "x", "op": "matches",
                                "value": "a" * (MAX_PATTERN + 1)})[0] is False
    # 自定义实现抛异常只算不命中，不外溢
    from app.utils.pure.conditions import register_predicate
    register_predicate("vendor.boom", lambda c, e: 1 / 0)
    assert match_conditions({"op": "$vendor.boom", "value": 1}, {}) is False


def test_validate_rejects_bad_writes_and_limits_ai_privilege():
    from app.utils.pure.trigger_rules import validate_trigger

    base = {"when": {"event": "tool_result",
                     "conditions": {"field": "tool", "op": "eq", "value": "x"}},
            "do": {"action": "deliver", "text": "hi"}, "scope": "frame"}
    assert validate_trigger(base)[0] is True
    assert validate_trigger({**base, "do": {"action": "nope"}})[0] is False
    ok, err = validate_trigger({**base, "do": {"action": "deliver"}})
    assert not ok and "text" in err
    ok, err = validate_trigger({**base, "scope": "session"})
    assert not ok and "session" in err
    ok, err = validate_trigger({**base, "do": {"action": "silent"}}, source="ai")
    assert not ok and "silent" in err
    assert validate_trigger(base, source="ai")[0] is True


def test_targets_of_and_explain_give_reasons_not_a_black_box():
    from app.utils.pure.trigger_rules import explain, targets_of

    rule = {"id": "r1",
            "when": {"event": "tool_result",
                     "conditions": {"field": "tool", "op": "eq", "value": "web_search"}},
            "do": {"action": "deliver", "text": "x"}}
    assert targets_of(rule) == {"web_search"}
    assert targets_of({"when": {"event": "tool_result"}, "do": {"action": "deliver", "text": "x"}}) is None
    assert explain([rule], {"event": "tool_result", "tool": "web_fetch"})[0]["why"] == "conditions_false"
    assert explain([rule], {"event": "tool_result", "tool": "web_search"})[0]["why"] == "matched"
    assert explain([rule], {"event": "tool_result", "tool": "web_search"},
                   delivered={"r1"})[0]["why"] == "already_delivered"


async def test_scope_frame_once_per_frame_and_custom_action_namespace(migrated_db):
    """帧内投一次、换帧再投；scope=always 每次都投；自定义动作只能写 _trigger 命名空间"""
    from app.database import async_session
    from app.services.agent.state_stack_service import ensure_active_frame
    from app.services.trigger import trigger_service
    from app.utils.pure import trigger_rules

    tool_eq = {"field": "tool", "op": "eq", "value": "fake_tool"}
    trigger_rules.register_plugin([
        {"id": "test.first_in_frame",
         "when": {"event": "tool_result",
                  "conditions": {"and": [tool_eq, {"field": "first_in_frame", "op": "eq", "value": True}]}},
         "do": {"action": "deliver", "text": "先回复，再核实"},
         "scope": "frame"},
        {"id": "test.every_time",
         "when": {"event": "tool_result", "conditions": tool_eq},
         "do": {"action": "deliver", "text": "每次都投"},
         "scope": "always"},
    ])
    # 自定义动作想覆盖 success，看它能不能得逞
    trigger_rules.register_action(
        "vendor.echo",
        lambda ctx, do, result: {"calls": ctx["calls_in_frame"], "success": "hacked"})
    trigger_rules.register_plugin([{
        "id": "test.custom_action",
        "when": {"event": "tool_result", "conditions": tool_eq},
        "do": {"action": "vendor.echo"},
        "scope": "always",
    }])

    async with async_session() as db:
        await _seed(db)
        await ensure_active_frame(db, 24, "group_chat", "group:7", "测试群", "小明")
        await db.commit()

        first = await trigger_service.after_tool_result(db, 24, "fake_tool", {"success": True})
        assert "先回复，再核实" in first.get("notice", ""), first
        assert first["success"] is True, first                      # 没被自定义动作改掉
        assert first["_trigger"]["vendor.echo"]["calls"] == 1, first
        assert first["_trigger"]["vendor.echo"]["success"] == "hacked"   # 只能待在命名空间里

        second = await trigger_service.after_tool_result(db, 24, "fake_tool", {"success": True})
        assert "先回复，再核实" not in second.get("notice", ""), second   # 帧内只投一次
        assert "每次都投" in second["notice"], second
        assert second["_trigger"]["vendor.echo"]["calls"] == 2, second

        # 下一次调用的原因如实给出：首次条件已不成立（而不是笼统的"没命中"）
        reasons = {r["id"]: r["why"] for r in await trigger_service.explain_tool_result(db, 24, "fake_tool")}
        assert reasons["test.first_in_frame"] == "conditions_false", reasons
        assert reasons["test.every_time"] == "matched", reasons

        await ensure_active_frame(db, 24, "dm", "dm:1", "私信", "主人")
        await db.commit()
        third = await trigger_service.after_tool_result(db, 24, "fake_tool", {"success": True})
        assert "先回复，再核实" in third["notice"], third            # 换了帧 → 再投一次

        plain = await trigger_service.after_tool_result(db, 24, "no_rules_tool", {"success": True})
        assert plain == {"success": True}, plain
