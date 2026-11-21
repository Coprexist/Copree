"""代理 LLM 门卫：解析、上下文顺序、留痕幂等、成本兜底的停用线。"""
import pytest
from sqlalchemy import text

pytestmark = pytest.mark.anyio

AGENT_ID, OWNER, AGENT_USER = 999031, 1, 999032


class _Agent:
    """判定只读三个字段，不必是 ORM 对象。"""

    id = AGENT_ID
    name = "值班员"
    current_system_prompt = "夜里不睡，说话很短。"


def _frame(**over):
    frame = {"type": "group_chat", "context_ref": "group:81", "label": "诗",
             "doing": "刷B站", "emotion_text": "有点困", "proxy_rules": ""}
    frame.update(over)
    return frame


def test_verdict_parsing():
    """认 JSON、认围栏、认前后废话；缺 wake 或多说一句都算判不了。"""
    from app.services.brain.proxy_judge import _parse_verdict

    v = _parse_verdict('{"wake": false, "why": "没点名我", "urgency": "low"}')
    assert v is not None and v.wake is False and v.reason == "没点名我" and v.urgency == "low"

    fenced = "```json" + chr(10) + '{"wake": true, "why": "在叫我"}' + chr(10) + "```"
    v = _parse_verdict(fenced)
    assert v is not None and v.wake is True and v.urgency == "normal"

    v = _parse_verdict('好的，判断：{"wake": true, "why": "问我在干嘛"}')
    assert v is not None and v.wake is True

    assert _parse_verdict("我觉得不用") is None
    assert _parse_verdict('{"why": "缺 wake"}') is None
    assert _parse_verdict("") is None


def test_context_keeps_stable_blocks_first():
    """判断 → 场景 → 群规 → 角色 → 心情 → 消息；消息按时间正序、新的在最后。

    顺序即缓存：变化越慢的越靠前，换群也只 miss 后面的部分。
    """
    from app.services.brain.proxy_judge import build_context

    history = [{"actor": "书爱", "content": "今天吃什么"}, {"actor": "你说", "content": "随便"}]
    system, user = build_context(_Agent(), _frame(), history, "在吗")

    text_all = system["content"]
    assert text_all.index("判断下面这条群消息") < text_all.index("你在群")
    assert text_all.index("你在群") < text_all.index("你是谁") < text_all.index("此刻心情")
    assert "有点困" in text_all and "刷B站" in text_all

    assert user["content"].strip().endswith("在吗")
    assert 0 < user["content"].index("今天吃什么") < user["content"].index("随便")


def test_proxy_applies_skips_forced_wakes():
    """@ 本人 / @all / 群公告 / 特别关心 不问代理——强制唤醒不该被模型复核。"""
    from app.ai.decider import ActionContext, _proxy_applies
    from app.config import settings

    def ctx(**over):
        base = {"event_type": "message", "agent_id": AGENT_ID}
        base.update(over)
        return ActionContext(**base)

    before = settings.proxy_wake_enabled
    try:
        settings.proxy_wake_enabled = False
        assert _proxy_applies(ctx()) is False

        settings.proxy_wake_enabled = True
        assert _proxy_applies(ctx()) is True
        assert _proxy_applies(ctx(is_mentioned=True)) is False
        assert _proxy_applies(ctx(is_at_all=True)) is False
        assert _proxy_applies(ctx(is_announcement=True)) is False
        assert _proxy_applies(ctx(is_priority_friend=True)) is False
    finally:
        settings.proxy_wake_enabled = before


def test_context_carries_group_rules_and_announcement():
    """群主写的规矩与公告进上下文：群规在角色之前（换人不换群规），空串不产出空段。"""
    from app.services.brain.proxy_judge import build_context

    system, _ = build_context(
        _Agent(), _frame(), [], "在吗",
        group_rules="这是角色扮演闲聊群，气氛话、表情、晚安也该接",
        announcement="本群禁止广告",
    )
    text_all = system["content"]
    assert "群主给这个群定的规矩" in text_all and "气氛话、表情、晚安也该接" in text_all
    assert "本群公告" in text_all and "本群禁止广告" in text_all
    assert text_all.index("群主给这个群定的规矩") < text_all.index("你是谁"), "群规比人稳定，排前面"

    bare, _ = build_context(_Agent(), _frame(), [], "在吗")
    assert "群主给这个群定的规矩" not in bare["content"]
    assert "本群公告" not in bare["content"]


def test_context_says_who_said_what_including_itself():
    """代理看的历史里，AI 自己的话必须写成「你」，不能原样丢英文记法 self

    2026-11-14 真机：AI 刚说"你随便发一张，我当场念给你听"，紧接着那张图代理判
    「只是发图，没叫我或派活」——它把 AI 自己的话读成了别人说的，于是接不上话题。
    """
    from app.services.brain.proxy_judge import build_context

    history = [
        {"actor": "user", "content": "鲸鲸可以看到图片了"},
        {"actor": "self", "content": "要是真能看了，你随便发一张，我当场念给你听"},
    ]
    system, user = build_context(_Agent(), _frame(), history, "[图片]")

    assert "- 你：要是真能看了" in user["content"], user["content"]
    assert "- 群成员：鲸鲸可以看到图片了" in user["content"]
    assert "self" not in user["content"], "英文 actor 不该出现在给模型的文本里"
    assert "「你：」开头的那几行是你自己" in system["content"], "得告诉代理那是它自己说的话"


def test_context_tells_the_proxy_what_came_with_the_message():
    """代理只读文本、图片被压成 [图片]：不点明"带了图片"它会当成没内容

    2026-11-14 真机：群里发图，代理判「只是发图，没叫我或派活」→ 不唤醒，AI 就此错过那张图。
    """
    from app.services.brain.proxy_judge import build_context

    system, user = build_context(
        _Agent(), _frame(), [], "[图片]", attachment_note="（这条带了 1 张图片）",
    )
    assert "（这条带了 1 张图片）" in user["content"]
    assert "同伴发来图片或文件" in system["content"], "判定准则里要说明'发图是给你看东西'"

    _, bare = build_context(_Agent(), _frame(), [], "[图片]")
    assert "这条带了" not in bare["content"], "没附件时不该凭空出现一句"


async def test_attachment_note_counts_images_and_files(migrated_db):
    """给代理的附件事实由消息行算：几张图、什么文件（正文里只有占位，数不出这些）"""
    from sqlalchemy import text

    from app.ai.decider import _attachment_note
    from app.database import async_session

    async with async_session() as db:
        await db.execute(text(
            "INSERT INTO groups (id, name, owner_type, owner_id, avatar_mode, include_ai_in_avatar) "
            "VALUES (81, '洛希安的茶话会小屋', 'human', 1, 'default', true)"
        ))
        await db.execute(text(
            "INSERT INTO messages (id, group_id, sender_type, sender_id, content, attachments) "
            "VALUES (5140, 81, 'human', 1, '[图片]', CAST(:att AS JSONB))"
        ), {"att": '[{"file_id": 190, "name": "cat.png", "mime_type": "image/png"}, '
                '{"file_id": 191, "name": "稿子.pdf", "mime_type": "application/pdf"}]'})
        await db.commit()
        note = await _attachment_note(db, 5140)

    assert "1 张图片" in note and "1 个文件（稿子.pdf）" in note, note


async def test_mention_annotated_marks_who_and_not_you(migrated_db):
    """给代理的正文里，@ 后面就地写清"是谁、不是你"——裸令牌会被它读成点名

    2026-11-14 群 77 真机：正文 `<@!120> 那你在这里发条语音吧……`，代理判
    「点名让我发语音，接我的话」并起身接话，实际 @ 的是大肥鱼（化学老师是 users 40）。
    注脚就地跟在 id 后面（与账本 <@!id>（名字）同一份名字来源），只多三个字。
    """
    from app.ai.decider import _mention_annotated
    from app.database import async_session

    async with async_session() as db:
        await db.execute(text(
            "INSERT INTO users (id, username, password_hash, type) "
            "VALUES (120, '大肥鱼', 'x', 'human')"
        ))
        await db.commit()

        body = await _mention_annotated(db, "<@!120> 那你在这里发条语音吧……")
        assert "<@!120>（大肥鱼）（不是你）" in body, body
        assert "那你在这里发条语音吧" in body, "正文一个字都不该动"

        unknown = await _mention_annotated(db, "<@!999123> 在吗")
        assert "<@!999123>（用户999123）（不是你）" in unknown, unknown

        plain = "没有 @ 的正文"
        assert await _mention_annotated(db, plain) == plain, "没 @ 就原样返回"


def test_enforce_scope_leaves_ambient_groups_in_shadow():
    """留白名单：总开关打开后，名单里的群只判不改，其余群按判定执行。"""
    from app.config import settings
    from app.services.brain.proxy_judge import enforced_for

    before = (settings.proxy_wake_enforce, settings.proxy_wake_shadow_groups)
    try:
        settings.proxy_wake_enforce = False
        settings.proxy_wake_shadow_groups = ""
        assert enforced_for(66) is False, "总开关没开就一律影子"
        assert enforced_for(None) is False

        settings.proxy_wake_enforce = True
        settings.proxy_wake_shadow_groups = "66, 81 ,,abc"
        assert enforced_for(66) is False, "诗与洛希安留白：只判不改"
        assert enforced_for(81) is False
        assert enforced_for(77) is True, "测试群这批该严的按判定执行"
        assert enforced_for(85) is True, "手写杂项不影响认得的群号"
        assert enforced_for(None) is True, "没有群号的判定不受名单约束"
    finally:
        settings.proxy_wake_enforce, settings.proxy_wake_shadow_groups = before


async def _seed(db):
    from db_reset import clear

    await clear(db, "proxy_judgements", "agents", "users")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES "
        "(:owner, '主人', 'x', 'human'), (:au, '测试AI', 'x', 'ai')"),
        {"owner": OWNER, "au": AGENT_USER})
    await db.execute(text(
        "INSERT INTO agents (id, owner_id, name, user_id, state) "
        "VALUES (:a, :o, '值班员', :u, 'active')"),
        {"a": AGENT_ID, "o": OWNER, "u": AGENT_USER})
    await db.commit()


async def test_judgement_recorded_once_per_message(migrated_db):
    """同一条消息判两次只留第一行：重投/重排不该再问一次模型。"""
    from app.database import async_session
    from app.services.brain import proxy_judge

    async with async_session() as db:
        await _seed(db)
        first = proxy_judge.ProxyVerdict(wake=False, reason="没点名我", model="flash")
        second = proxy_judge.ProxyVerdict(wake=True, reason="改主意了", model="flash")
        await proxy_judge.record(db, AGENT_ID, _frame(), message_id=4242, verdict=first)
        await proxy_judge.record(db, AGENT_ID, _frame(), message_id=4242, verdict=second)
        await db.commit()

        rows = (await db.execute(text(
            "SELECT count(*) FROM proxy_judgements WHERE agent_id = :a"), {"a": AGENT_ID})).scalar()
        assert rows == 1

        cached = await proxy_judge.cached_verdict(db, AGENT_ID, 4242)
        assert cached is not None and cached["wake"] is False and cached["reason"] == "没点名我"


async def test_worthwhile_stops_when_proxy_saves_little(migrated_db):
    """拦下率不到下限就停用代理；fallback 行不进统计。"""
    from app.config import settings
    from app.database import async_session
    from app.services.brain import proxy_judge

    mins = (settings.proxy_wake_sample_min, settings.proxy_wake_sample_window,
            settings.proxy_wake_min_saving_ratio)
    settings.proxy_wake_sample_min, settings.proxy_wake_sample_window = 4, 10
    settings.proxy_wake_min_saving_ratio = 0.1
    try:
        async with async_session() as db:
            await _seed(db)
            wake = proxy_judge.ProxyVerdict(wake=True, reason="都该醒", model="flash")
            quiet = proxy_judge.ProxyVerdict(wake=False, reason="不用来", model="flash")
            fallback = proxy_judge.ProxyVerdict(wake=True, reason="代理挂了", fallback=True)

            for i in range(10):
                await proxy_judge.record(db, AGENT_ID, _frame(), message_id=5000 + i, verdict=wake)
            await proxy_judge.record(db, AGENT_ID, _frame(), message_id=5999, verdict=fallback)
            await db.commit()
            assert await proxy_judge.worthwhile(db, AGENT_ID) is False

            for i in range(6):
                await proxy_judge.record(db, AGENT_ID, _frame(), message_id=6000 + i, verdict=quiet)
            await db.commit()
            # 窗口是最近 10 条：3 条 wake + 1 条 fallback + 6 条 quiet；
            # fallback 是被排除的（它不是代理的判断），所以判了 9 条、醒了 3 条
            stats = await proxy_judge.stats(db, AGENT_ID)
            assert stats["total"] == 9 and stats["wake"] == 3 and stats["saving"] == 6
            assert await proxy_judge.worthwhile(db, AGENT_ID) is True
    finally:
        (settings.proxy_wake_sample_min, settings.proxy_wake_sample_window,
         settings.proxy_wake_min_saving_ratio) = mins


GROUP_ID = 999081


async def _seed_frame_and_group(db):
    """起一个群和它的 AI。提醒不再依赖帧实例——群帧一轮一换，拿它做幂等会连投六轮。"""
    from db_reset import clear

    await clear(db, "agent_history_entries", "ai_conversation_logs", "proxy_judgements",
                "agents", "groups", "users")
    await db.execute(text(
        "INSERT INTO users (id, username, password_hash, type) VALUES "
        "(:owner, '主人', 'x', 'human'), (:au, '测试AI', 'x', 'ai')"),
        {"owner": OWNER, "au": AGENT_USER})
    await db.execute(text(
        "INSERT INTO agents (id, owner_id, name, user_id, state) "
        "VALUES (:a, :o, '值班员', :u, 'active')"),
        {"a": AGENT_ID, "o": OWNER, "u": AGENT_USER})
    await db.execute(text(
        "INSERT INTO groups (id, name, owner_type, owner_id, avatar_mode, include_ai_in_avatar) "
        "VALUES (:g, '诗', 'human', :o, 'default', true)"),
        {"g": GROUP_ID, "o": OWNER})
    await db.commit()


async def _log_wakes(db, n: int):
    """往调用日志里补 n 次真实唤醒（origin=call 才算）"""
    for _ in range(n):
        await db.execute(text(
            "INSERT INTO ai_conversation_logs (agent_id, group_id, conversation_type, messages, origin) "
            "VALUES (:a, :g, 'group', '[]'::jsonb, 'call')"),
            {"a": AGENT_ID, "g": GROUP_ID})
    await db.commit()


async def _reminder_notes(db) -> int:
    return (await db.execute(text(
        "SELECT count(*) FROM agent_history_entries WHERE agent_id = :a AND ref LIKE 'remind:%'"),
        {"a": AGENT_ID})).scalar()


async def test_reminder_waits_for_threshold(migrated_db):
    """不到阈值不劝；到了只劝一次——幂等键在账本里，所以帧换了也不会重复投。"""
    from app.config import settings
    from app.database import async_session
    from app.services.brain.frame_reminder import deliver_reminder

    threshold = settings.proxy_wake_remind_threshold
    settings.proxy_wake_remind_threshold = 3
    try:
        async with async_session() as db:
            await _seed_frame_and_group(db)
            await _log_wakes(db, 2)
            assert await deliver_reminder(db, _Agent(), GROUP_ID) is False

            await _log_wakes(db, 1)
            assert await deliver_reminder(db, _Agent(), GROUP_ID) is True
            assert await _reminder_notes(db) == 1

            # 同一段上下文不再劝（帧实例换掉也一样：记录在账本，不在帧上）
            assert await deliver_reminder(db, _Agent(), GROUP_ID) is False
            assert await _reminder_notes(db) == 1
    finally:
        settings.proxy_wake_remind_threshold = threshold


async def test_reminder_skipped_when_disabled(migrated_db):
    """阈值设 0 = 关掉这条提醒"""
    from app.config import settings
    from app.database import async_session
    from app.services.brain.frame_reminder import deliver_reminder

    threshold = settings.proxy_wake_remind_threshold
    settings.proxy_wake_remind_threshold = 0
    try:
        async with async_session() as db:
            await _seed_frame_and_group(db)
            await _log_wakes(db, 5)
            assert await deliver_reminder(db, _Agent(), GROUP_ID) is False
            assert await _reminder_notes(db) == 0
    finally:
        settings.proxy_wake_remind_threshold = threshold

async def _seed_messages(db, *, count: int, minutes_ago: int, prefix: str = "m"):
    """往账本里塞 count 条对话（created_at 往前推 minutes_ago 分钟）。"""
    last = (await db.execute(text(
        "SELECT coalesce(max(seq), 0) FROM agent_history_entries WHERE agent_id = :a"),
        {"a": AGENT_ID})).scalar() or 0
    for i in range(count):
        await db.execute(text(
            "INSERT INTO agent_history_entries "
            "(agent_id, context_ref, seq, kind, actor, content, ref, flags, created_at) "
            "VALUES (:a, :c, :s, 'message', 'user', :t, '', '{}'::jsonb, "
            "        now() - make_interval(mins => :m))"),
            {"a": AGENT_ID, "c": f"group:{GROUP_ID}", "s": last + i + 1,
             "t": f"{prefix}{i}", "m": minutes_ago})
    await db.commit()


async def test_recent_ledger_takes_the_smaller_limit(migrated_db):
    """条数与时间窗取小：热闹群只给 10 条，冷群不给 5 分钟以外的话。"""
    from app.database import async_session
    from app.services.brain.proxy_judge import recent_ledger

    async with async_session() as db:
        await _seed_frame_and_group(db)
        await _seed_messages(db, count=15, minutes_ago=0)
        got = await recent_ledger(db, AGENT_ID, f"group:{GROUP_ID}")
        assert len(got) == 10 and got[-1]["content"] == "m14" and got[0]["content"] == "m5"

        await _seed_frame_and_group(db)          # 清空重来
        await _seed_messages(db, count=3, minutes_ago=10, prefix="old")
        await _seed_messages(db, count=2, minutes_ago=0, prefix="new")
        got = await recent_ledger(db, AGENT_ID, f"group:{GROUP_ID}")
        assert [g["content"] for g in got] == ["new0", "new1"]


async def test_recent_ledger_skips_the_trigger_message(migrated_db):
    """待判的那条若已经在账本尾巴里，别叠一遍。"""
    from app.database import async_session
    from app.services.brain.proxy_judge import recent_ledger

    async with async_session() as db:
        await _seed_frame_and_group(db)
        await _seed_messages(db, count=1, minutes_ago=0, prefix="前面那句 ")
        await _seed_messages(db, count=1, minutes_ago=0, prefix="看看这个 ")
        plain = await recent_ledger(db, AGENT_ID, f"group:{GROUP_ID}")
        assert len(plain) == 2
        deduped = await recent_ledger(db, AGENT_ID, f"group:{GROUP_ID}",
                                      exclude_text="看看这个 0")
        assert [g["content"] for g in deduped] == ["前面那句 0"]


async def test_recent_ledger_ignores_non_conversation_entries(migrated_db):
    """账本里的缺口、便签、提醒不是"他们在聊什么"。"""
    from app.database import async_session
    from app.services.brain.proxy_judge import recent_ledger

    async with async_session() as db:
        await _seed_frame_and_group(db)
        await _seed_messages(db, count=1, minutes_ago=0, prefix="真正的对话 ")
        await db.execute(text(
            "INSERT INTO agent_history_entries "
            "(agent_id, context_ref, seq, kind, actor, content, ref, flags, created_at) "
            "VALUES (:a, :c, 99, 'note', 'system', '系统提醒：设个界限', '', '{}'::jsonb, now())"),
            {"a": AGENT_ID, "c": f"group:{GROUP_ID}"})
        await db.commit()
        got = await recent_ledger(db, AGENT_ID, f"group:{GROUP_ID}")
        assert [g["content"] for g in got] == ["真正的对话 0"]
