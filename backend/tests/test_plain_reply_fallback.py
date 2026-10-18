"""模型只说了人话、没调工具时的兜底：记账本，不替它发

背景（2026-10-18，会话 1_163）：AI 偶尔整轮只返回文字（intent 解析失败）。旧逻辑连注 10 次
system_reminder 要它改用工具重说；中间一版改成"直接把这段文字发出去"，但正文可能是它给自己
记的过程，也可能是准备发给另一个会话的话——平台分不清"这句是给谁的"，代发就是替它说错人。
最终口径：提醒一次；仍然只说话就把「这段话 + 平台当时的提醒」写进账本，下一轮它自己补发。
"""


def test_unsent_text_goes_to_the_ledger_with_the_reminder():
    from app.utils.pure.history import unsent_text_entry

    entry = unsent_text_entry("在，咋了", "文字不会自动发出去：私信请调用 send_dm")
    assert entry["kind"] == "note" and entry["actor"] == "system"
    assert entry["flags"] == {"drop_on_unlock": True}, "只活到解锁：compact 之后它就是过期信息"
    assert "【没发出去的话】" in entry["content"]
    assert "> 在，咋了" in entry["content"]
    assert "平台当时的提醒：文字不会自动发出去：私信请调用 send_dm" in entry["content"]

    # 空正文不能变成一条只有提醒的假条目；长正文截断在 8 行，别把整段过程抄进账本
    assert "> " not in unsent_text_entry("", "提醒")["content"]
    many = unsent_text_entry("\n".join(f"第{i}行" for i in range(20)), "提醒")
    assert "第7行" in many["content"] and "第8行" not in many["content"]


def test_auto_send_path_is_gone():
    """账本取代了"替它发出去"。有人把这套投递加回来，这条会红——它会绕过"这句是给谁的"这个判断。"""
    import app.ai.executor as ex

    assert not hasattr(ex, "_deliver_plain_text")
    assert not hasattr(ex, "_looks_like_monologue")


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
