"""决策层契约：不绑世界也能跑、do 各动作落到正确的执行者、notify 语义。

情景表与分派规则见 docs/dev/decision_layer.md。
"""
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


async def _save(db, rule):
    from app.services.world.decision_skill import save_decision_rule
    return await save_decision_rule(db, "agent", 24, rule)


def _incoming(content="我来签到", mention=False):
    from app.services.world.decision_skill import build_group_message_ctx
    return build_group_message_ctx(content, 1, "小明", "human", 7, is_mention=mention)


async def test_agent_rule_fires_without_any_world(migrated_db):
    """入驻 AI 的决策技能不再要求绑定世界：World=None 也照跑"""
    from app.database import async_session
    from app.services.world.decision_skill import run_decision_engine

    async with async_session() as db:
        await _seed(db)
        ok, err = await _save(db, {
            "name": "签到", "when": {"event": "group_message", "conditions": {"content_contains": "签到"}},
            "do": {"action": "reply_template", "reply": "已记录"}, "notify": False,
        })
        assert ok, err
        dec = await run_decision_engine(db, "agent", 24, None, "group_message", _incoming())
        assert dec["hit"] and dec["handled"] and dec["reply"] == "已记录", dec


async def test_notify_true_still_wakes_the_owner_with_the_result(migrated_db):
    """notify=true：动作照跑，但本体仍被唤醒，且看得到执行结果（不重复劳动）"""
    from app.database import async_session
    from app.services.world.decision_skill import run_decision_engine

    async with async_session() as db:
        await _seed(db)
        await _save(db, {
            "name": "签到", "when": {"event": "group_message", "conditions": {"content_contains": "签到"}},
            "do": {"action": "reply_template", "reply": "已记录"}, "notify": True,
        })
        dec = await run_decision_engine(db, "agent", 24, None, "group_message", _incoming())
        assert dec["hit"] and dec["handled"] is False, dec
        assert "签到" in dec["note"] and "仍需你亲自判断" in dec["note"], dec["note"]


async def test_call_tool_runs_as_the_ai_itself(migrated_db):
    """call_tool 对入驻 AI 走平台工具分发（它自己的身份），不再要求世界工具"""
    from app.database import async_session
    from app.services.world.decision_skill import run_decision_engine

    async with async_session() as db:
        await _seed(db)
        await _save(db, {
            "name": "看闹钟", "when": {"event": "group_message"},
            "do": {"action": "call_tool", "name": "list_alarms", "arguments": {}}, "notify": True,
        })
        dec = await run_decision_engine(db, "agent", 24, None, "group_message", _incoming())
        assert dec["handled"] is False, dec
        assert dec["result"]["success"] is True, dec["result"]
        assert dec["result"]["result"].get("alarms") == [], dec["result"]


async def test_run_script_says_what_stdout_says(migrated_db):
    """run_script 在 AI 自己的文件空间里跑；要发的话由 stdout 的 JSON 给"""
    from app.database import async_session
    from app.services.world.decision_skill import run_decision_engine

    async with async_session() as db:
        await _seed(db)
        await _save(db, {
            "name": "算一下", "when": {"event": "group_message"},
            "do": {"action": "run_script",
                   "code": "import json\nprint(json.dumps({'reply': '脚本说的'}))"},
            "notify": False,
        })
        dec = await run_decision_engine(db, "agent", 24, None, "group_message", _incoming())
        assert dec["handled"] is True and dec["reply"] == "脚本说的", dec


async def test_unknown_event_is_rejected_with_the_scenario_list(migrated_db):
    """事件名写错当场拒绝并列出可选值——否则规则会安静地永远不触发"""
    from app.database import async_session

    async with async_session() as db:
        await _seed(db)
        ok, err = await _save(db, {
            "name": "错的", "when": {"event": "group_msg"},
            "do": {"action": "reply_template", "reply": "x"}, "notify": False,
        })
        assert not ok and "group_message" in err and "member_join" in err, err


async def test_batch_preload_matches_per_entity_read(migrated_db):
    """批量预取与逐个读必须同源：全量消息下靠它省掉 N 次查库"""
    from app.database import async_session
    from app.services.world.decision_skill import get_decision_rules, load_rules_map

    async with async_session() as db:
        await _seed(db)
        for name in ("甲", "乙"):
            await _save(db, {
                "name": name, "when": {"event": "group_message"},
                "do": {"action": "reply_template", "reply": name}, "notify": False,
            })
        batched = await load_rules_map(db, "agent", [24, 999])
        single = await get_decision_rules(db, "agent", 24)
        assert batched[24] == single and len(batched[24]) == 2, batched
        assert batched[999] == [], batched

async def test_member_join_is_greeted_through_the_rule(migrated_db):
    """入群情景：规则命中即代发欢迎语——这条路上平台本来不会唤醒任何 AI"""
    from sqlalchemy import select, text

    from app.database import async_session
    from app.models.message import Message

    async with async_session() as db:
        await _seed(db)
        await db.execute(text(
            "INSERT INTO groups (id, name, owner_type, owner_id, avatar_mode, include_ai_in_avatar, "
            "searchable, auto_approve_join, approve_invites) "
            "VALUES (7, '欢迎自测群', 'human', 1, 'default', true, true, true, true)"))
        await db.execute(text(
            "INSERT INTO group_members (group_id, member_type, member_id, role) "
            "VALUES (7, 'ai', 2, 'member')"))
        await db.execute(text(
            "INSERT INTO users (id, username, password_hash, type) VALUES (3, '新同学', 'x', 'human')"))
        await db.commit()
        ok, err = await _save(db, {
            "name": "欢迎", "when": {"event": "member_join", "conditions": {"member_name": "新同学"}},
            "do": {"action": "reply_template", "reply": "欢迎新同学"}, "notify": False,
        })
        assert ok, err

        from app.chat.gm import add_member
        await add_member(db, 7, "human", 3)
        await db.commit()

        rows = [(r[0], r[1]) for r in (await db.execute(
            select(Message.content, Message.sender_type).where(Message.group_id == 7)
        )).all()]
        assert ("欢迎新同学", "ai") in rows, rows


async def test_scheduled_scenario_matches_the_alarm_context(migrated_db):
    """定时情景：闹钟事件经同一引擎匹配（闹钟链路在唤醒前先问它）"""
    from app.database import async_session
    from app.services.world.decision_skill import run_decision_engine

    async with async_session() as db:
        await _seed(db)
        ok, err = await _save(db, {
            "name": "早安", "when": {"event": "scheduled", "conditions": {"task_contains": "早安"}},
            "do": {"action": "reply_template", "reply": "早"}, "notify": False,
        })
        assert ok, err
        dec = await run_decision_engine(db, "agent", 24, None, "scheduled",
                                        {"event": "scheduled", "trigger": "alarm", "task": "发早安", "alarm_id": 1})
        assert dec["hit"] and dec["handled"] and dec["reply"] == "早", dec


async def test_silent_ends_the_message_without_replying_or_waking(migrated_db):
    """silent：命中即到此为止——不代发（不回复）、也不唤醒本体"""
    from app.database import async_session
    from app.services.world.decision_skill import run_decision_engine

    async with async_session() as db:
        await _seed(db)
        ok, err = await _save(db, {
            "name": "不接茬", "when": {"event": "group_message", "conditions": {"content": "在？"}},
            "do": {"action": "silent"}, "notify": False,
        })
        assert ok, err
        dec = await run_decision_engine(db, "agent", 24, None, "group_message", _incoming(content="在？"))
        assert dec["hit"] and dec["handled"] and dec["reply"] == "", dec


async def test_silent_conflicts_with_notify(migrated_db):
    """silent 与 notify=true 互斥：静默就是不唤醒本体，要它自己判断就别写这条规则"""
    from app.database import async_session

    async with async_session() as db:
        await _seed(db)
        ok, err = await _save(db, {
            "name": "矛盾", "when": {"event": "group_message"},
            "do": {"action": "silent"}, "notify": True,
        })
        assert not ok and "notify" in err, err


def test_keyword_primitives_share_one_condition_engine():
    """全等 / 包含 / 相似 / 长度 / 非与或：同一棵条件树（决策技能与触发规则共用一份求值）"""
    from app.services.world.decision_skill import build_group_message_ctx
    from app.utils.pure.conditions import match_conditions

    ctx = build_group_message_ctx("<@!1> 签到了签到啦", 1, "小明", "human", 7)
    # @ 令牌先收掉：QQ 里用户 @ 你是常态，拿原串做全等永远对不上
    assert ctx["content_clean"] == "签到了签到啦" and ctx["content_len"] == 6
    assert match_conditions({"content_clean": "签到"}, ctx) is False        # 全等 = 整条消息
    assert match_conditions({"content_clean": "签到了签到啦"}, ctx) is True
    assert match_conditions({"content_clean_contains": "签到"}, ctx) is True
    assert match_conditions({"field": "content_clean", "op": "similar", "value": "签到"}, ctx) is True
    assert match_conditions({"content_len_lte": 6}, ctx) is True
    assert match_conditions({"content_len_lte": 5}, ctx) is False
    assert match_conditions(
        {"and": [{"content_clean_contains": "签到"}, {"not": {"is_mention": True}}]}, ctx) is True


def test_similar_rejects_a_broken_threshold():
    """阈值写错当场拒绝：安静地不命中比报错难查得多"""
    from app.utils.pure.conditions import validate_conditions

    ok, err = validate_conditions(
        {"field": "content", "op": "similar", "value": {"text": "签到", "ratio": 2}})
    assert not ok and "0~1" in err, err
    ok, err = validate_conditions({"field": "content", "op": "similar", "value": ""})
    assert not ok and "关键词" in err, err


async def test_every_scenario_gets_the_clock(migrated_db):
    """时间接口：由引擎统一补上（六个情景各写一遍迟早漏一个）"""
    from app.database import async_session
    from app.services.world.decision_skill import run_decision_engine

    async with async_session() as db:
        await _seed(db)
        ok, err = await _save(db, {
            "name": "几点都行", "when": {"event": "group_message", "conditions": {"hour_gte": 0}},
            "do": {"action": "reply_template", "reply": "好"}, "notify": False,
        })
        assert ok, err
        dec = await run_decision_engine(db, "agent", 24, None, "group_message", _incoming())
        assert dec["hit"] and dec["reply"] == "好", dec


async def test_reply_template_names_the_sender(migrated_db):
    """零唤醒的固定回复也能叫出对方名字；认不出的 {…} 原样留着（花括号可能是字面意思）"""
    from app.database import async_session
    from app.services.world.decision_skill import run_decision_engine

    async with async_session() as db:
        await _seed(db)
        ok, err = await _save(db, {
            "name": "签到",
            "when": {"event": "group_message", "conditions": {"content_clean_contains": "签到"}},
            "do": {"action": "reply_template", "reply": "{sender_name} 已记录，{unknown} 原样"},
            "notify": False,
        })
        assert ok, err
        dec = await run_decision_engine(db, "agent", 24, None, "group_message", _incoming())
        assert dec["reply"] == "小明 已记录，{unknown} 原样", dec
