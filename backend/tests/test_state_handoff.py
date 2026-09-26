"""原文尾巴交接：切会话时不丢线头

设计：
- 切走时自动记住那段对话的最后几轮**原文**，不额外让 LLM 生成摘要（省一次调用、
  也不会把原文改写成跑偏的概述）；
- 切回来时只注入一轮，标签写成禁令：群里的 AI 看到私信的尾巴，很容易顺手在群里
  答一句私信的内容（串台），所以要明说「不要在别处回应它」；
- 尾巴是**临时性**交接：长期该记住的东西归 AI 自己的提示词（update_self_config），
  不往尾巴里堆。
"""


def _msgs(*pairs):
    out = [{"role": "system", "content": "规矩"}]
    for role, text in pairs:
        out.append({"role": role, "content": text})
    return out


def test_frame_tail_keeps_only_last_exchanges():
    """只带最后两轮（含 AI 的回复），更早的不进尾巴"""
    from app.utils.pure.state_stack import frame_tail

    msgs = _msgs(
        ("user", "第一轮问题"), ("assistant", "第一轮回答"),
        ("user", "第二轮问题"), ("assistant", "第二轮回答"),
        ("user", "第三轮问题"), ("assistant", "第三轮回答"),
    )
    tail = frame_tail(msgs, max_exchanges=2)

    assert tail == ["第二轮问题", "第二轮回答", "第三轮问题", "第三轮回答"], tail
    assert all("第一轮" not in t for t in tail)


def test_frame_tail_ignores_system_and_empty():
    """system 段（规矩/时间/摘要）不是对话原文，空内容的消息也不占位"""
    from app.utils.pure.state_stack import frame_tail

    msgs = _msgs(("user", "书爱: 暗号是 7788"))
    msgs += [
        {"role": "system", "content": "## 📋 当前状态"},
        {"role": "assistant", "content": ""},
        {"role": "tool", "content": "{}"},
    ]
    tail = frame_tail(msgs, max_exchanges=2)

    assert tail == ["书爱: 暗号是 7788"], tail


def test_frame_tail_drops_oldest_on_budget():
    """超字数从最旧端丢——尾巴的意义就是「最新说到哪」"""
    from app.utils.pure.state_stack import frame_tail

    msgs = _msgs(("user", "旧" * 500), ("user", "新" * 500))
    tail = frame_tail(msgs, max_exchanges=2, max_chars=600)

    assert len(tail) == 1 and tail[0].startswith("新"), tail


def test_frame_tail_keeps_long_last_message():
    """最后一条自己就超预算时也不能丢——实测 AI 上一轮的长回复单条上千字"""
    from app.utils.pure.state_stack import frame_tail

    msgs = _msgs(("assistant", "长" * 3000))
    tail = frame_tail(msgs, max_exchanges=2, max_chars=600)

    assert len(tail) == 1 and tail[0].startswith("长" * 10), len(tail)
    assert "已截断" in tail[0] and len(tail[0]) < 700


def test_frame_tail_default_window_carries_a_promised_contract():
    """默认窗口要装得下"约好的事"：实测里暗号契约在倒数第 3 个用户轮上，2 轮窗口装不下"""
    from app.utils.pure.state_stack import frame_tail

    msgs = _msgs(
        ("user", "约个暗号：说「少宇」你要回「牛逼！…」"),   # 契约（第 1 个用户轮）
        ("assistant", "记下了"),
        ("user", "另外道具也写进提示词"),                      # 2
        ("assistant", "好"),
        ("user", "身份这条别加"),                              # 3
        ("assistant", "明白"),
        ("user", "那我现在在群里说一句少宇"),                  # 4（最新）
    )
    tail = frame_tail(msgs)

    assert any("约个暗号" in t for t in tail), tail
    assert tail[-1].endswith("那我现在在群里说一句少宇")


def test_handoff_tail_says_do_not_answer_elsewhere():
    """标签是禁令 + 带上「从哪来」，否则 AI 会在当前会话复述上一段对话"""
    from app.utils.pure.state_stack import format_handoff_tail

    block = format_handoff_tail("私信「书爱」", ["书爱: 暗号是 7788"], reason="收到群成员的消息")

    assert "私信「书爱」" in block
    assert "在别处回应" in block or "不要在当前会话里回应它" in block, block
    assert "书爱: 暗号是 7788" in block
    assert "收到群成员的消息" in block
    assert format_handoff_tail("私信「书爱」", []) == ""


def test_handoff_tail_allows_executing_a_promised_contract():
    """只写禁令会让 AI 明明看见暗号也不敢答（2026-09-25 实测）——必须同时允许履约"""
    from app.utils.pure.state_stack import format_handoff_tail

    block = format_handoff_tail("私信「管理员」", ["你: 说「少宇」就回那句"])

    assert "按约定执行" in block, block
    assert "cross_state_note" in block, "顺带要把「临时约定该记哪儿」告诉 AI"


def test_state_summary_prefers_human_label():
    """摘要里显示「私信「书爱」」而不是机器码 dm:s1"""
    from app.utils.pure.state_stack import make_state_frame, format_state_stack_summary

    frame = make_state_frame(type_="dm", context_ref="dm:s1", label="私信「书爱」",
                             why="收到书爱的消息", doing="在私信里回复书爱")
    summary = format_state_stack_summary([frame])

    assert "私信「书爱」" in summary
    assert "dm:s1" not in summary


async def test_frame_turn_context_stores_then_consumes_once():
    """一轮只做两件事：存本会话尾巴、消费上一段对话的尾巴（消费后不再重复注入）"""
    from app.services.agent import state_stack_service as sss

    stored = {}

    async def fake_get_stack(db, agent_id):
        return stored["stack"]

    async def fake_set_stack(db, agent_id, stack):
        stored["stack"] = stack

    real_get, real_set = sss._get_stack, sss._set_stack
    sss._get_stack, sss._set_stack = fake_get_stack, fake_set_stack
    try:
        stored["stack"] = [{
            "id": "f1", "type": "dm", "context_ref": "s1", "label": "私信「书爱」",
            "why": "收到群成员的消息", "doing": "在私信里回复书爱",
            "handoff": {"from_type": "group_chat", "from_label": "群「测试群」",
                        "tail": ["群成员: 你刚才私信说啥了"]},
            "completed_handoff": {},
        }]

        first = await sss.frame_turn_context(object(), 1, "s1", ["书爱: 暗号是 7788"])
        assert "群「测试群」" in first and "你刚才私信说啥了" in first
        assert stored["stack"][0]["tail"] == ["书爱: 暗号是 7788"]      # 本会话尾巴已存
        assert not (stored["stack"][0]["handoff"] or {}).get("tail")     # 已消费清空

        second = await sss.frame_turn_context(object(), 1, "s1", ["书爱: 暗号是 7788"])
        assert second == "", "尾巴是临时交接，只注入一轮，不能每轮重复喂"
    finally:
        sss._get_stack, sss._set_stack = real_get, real_set


async def test_frame_turn_context_does_not_write_to_other_conversation_frame():
    """栈顶是别的会话时不许硬写尾巴（切换没走完时会把 A 的原文记成 B 的）"""
    from app.services.agent import state_stack_service as sss

    stored = {"stack": [{"id": "f1", "type": "group_chat", "context_ref": "group:7", "tail": ["群的旧尾巴"]}]}
    writes = []

    async def fake_get_stack(db, agent_id):
        return stored["stack"]

    async def fake_set_stack(db, agent_id, stack):
        writes.append(stack)

    real_get, real_set = sss._get_stack, sss._set_stack
    sss._get_stack, sss._set_stack = fake_get_stack, fake_set_stack
    try:
        assert await sss.frame_turn_context(object(), 1, "s1", ["私信的原文"]) == ""
        assert stored["stack"][0]["tail"] == ["群的旧尾巴"]
        assert writes == [], "没变化就不该写库"
    finally:
        sss._get_stack, sss._set_stack = real_get, real_set
