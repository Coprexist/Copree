"""模型只说了人话、没调工具时的兜底投递：消息不能凭空消失

背景（2026-10-18）：新建的 AI 在私信里偶尔整轮只返回文字（intent 解析失败），旧逻辑最多注入
10 次 system_reminder 要它改用工具重说；结果用户等两三分钟，等来的是沉默或者一段乱码。
现在提醒只发一次，第二次仍然只说人话就把这句直接发出去。
"""


class _Agent:
    id = 51
    name = "陈屿"
    user_id = 163


def _install(fake):
    """把 dispatch_tool_call 换成假实现，返回还原函数（runner 里没有 monkeypatch fixture）。"""
    import app.services.tool_registry as tr

    old = tr.dispatch_tool_call
    tr.dispatch_tool_call = fake

    def restore():
        tr.dispatch_tool_call = old

    return restore


def test_dm_peer_is_the_other_side_of_the_session_id():
    from app.ai.executor import _dm_peer_id

    assert _dm_peer_id("1_163", 163) == 1
    assert _dm_peer_id("1_163", 1) == 163
    assert _dm_peer_id("1_163", 999) is None, "自己不在这个会话里 → 宁可投不出去，也别投错人"
    assert _dm_peer_id(None, 1) is None
    assert _dm_peer_id("garbage", 1) is None


async def test_dm_leftover_messages_are_requeued():
    """AI 正忙时到达的第二条私信不能被吞：轮末要自己排一轮（群聊侧早就有这道兜底）

    实测（2026-10-18，会话 1_163）：AI 回复完第一条后 2 秒又来一条，触发时它仍被标成"在跑"，
    消息被注入中断缓冲；本轮已经过了最后一次注入点，之后没人再来叫 —— 这条消息就消失了。
    """
    from app.ai import executor
    from app.ai import response_worker as rw

    executor._pending_interrupts.clear()
    while not rw.message_queue.empty():
        rw.message_queue.get_nowait()

    await executor.add_pending_interrupt(70, {
        "type": "user_message", "content": "第二条", "message_id": 4139,
        "session_id": "1_170", "sender_id": 1,
    })
    await executor.add_pending_interrupt(70, {
        "type": "user_message", "content": "别的会话", "message_id": 4138,
        "session_id": "1_999", "sender_id": 1,
    })

    try:
        await rw._requeue_leftover_dm_interrupts(70, "1_170", 2)

        event = rw.message_queue.get_nowait()
        assert event["conversation_type"] == "dm"
        assert event["session_id"] == "1_170"
        assert event["content"] == "第二条" and event["message_id"] == 4139
        assert rw.message_queue.empty(), "只重排本会话的那一条"

        leftover = executor._pending_interrupts.get(70) or []
        assert [m["session_id"] for m in leftover] == ["1_999"], "别的会话不归这次收尾管"
    finally:
        executor._pending_interrupts.clear()
        while not rw.message_queue.empty():
            rw.message_queue.get_nowait()


async def test_plain_text_in_dm_is_delivered_as_send_dm():
    from app.ai.executor import _deliver_plain_text

    calls = []

    async def fake(db, agent_id, group_id, tool_name, args, context):
        calls.append((agent_id, group_id, tool_name, args))
        return {"success": True, "message_id": 42}

    restore = _install(fake)
    try:
        got = await _deliver_plain_text(None, _Agent(), "dm", None, "1_163", "  在，咋了 ", {})
    finally:
        restore()

    assert got and got["message_id"] == 42
    assert calls == [(51, None, "send_dm", {"target_user_id": 1, "content": "在，咋了"})], \
        "内容去掉首尾空白，走 send_dm，收件人是会话另一端"


async def test_plain_text_in_group_is_delivered_as_send_gm():
    from app.ai.executor import _deliver_plain_text

    calls = []

    async def fake(db, agent_id, group_id, tool_name, args, context):
        calls.append((agent_id, group_id, tool_name, args))
        return {"success": True, "message_id": 7}

    restore = _install(fake)
    try:
        got = await _deliver_plain_text(None, _Agent(), "group", 12, None, "在", {})
    finally:
        restore()

    assert got and calls == [(51, 12, "send_gm", {"content": "在"})]


async def test_plain_text_is_dropped_safely_when_delivery_fails():
    from app.ai.executor import _deliver_plain_text

    calls = []

    async def fake(db, agent_id, group_id, tool_name, args, context):
        calls.append(tool_name)
        return {"error": True, "message": "无权在此会话中发言"}

    restore = _install(fake)
    try:
        assert await _deliver_plain_text(None, _Agent(), "dm", None, "1_163", "在", {}) is None
        assert await _deliver_plain_text(None, _Agent(), "dm", None, "1_163", "   ", {}) is None
        assert await _deliver_plain_text(None, _Agent(), "alarm", None, None, "在", {}) is None
    finally:
        restore()

    assert calls == ["send_dm"], "空文本 / 非群聊私信的场景不该再发一次"

    async def boom(*a, **kw):
        raise RuntimeError("boom")

    restore = _install(boom)
    try:
        assert await _deliver_plain_text(None, _Agent(), "group", 12, None, "在", {}) is None, \
            "投递异常不能把整轮打挂"
    finally:
        restore()
