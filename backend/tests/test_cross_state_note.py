"""跨状态便签：投递制（临时、有时效；投进会话就固化在前缀里）

用户 2026-09-25 定的语义（对齐 docs/dev/capability_lazy_loading.md 的锁）：
- 记录只管「还能不能投递」：写下后 40 次 API 调用内有效，过期就不再投给任何会话
  ——这就是"清除过时便签"，AI 一直忙工作时不会再收到它；
- 一旦投进某个会话，就把内容抄进那段会话的上下文，之后每轮字节一致地待在前缀里
  （缓存命中、不重复花 token），直到那段对话 compact/clear 解锁重建；
- 所以过期**不**从已投递的会话里撤（撤了反而断前缀缓存），只有 AI 主动删除才撤。
"""


def test_delivery_window_is_40_calls():
    from app.utils.pure.cross_state_note import make_note, is_expired, NOTE_TTL_CALLS

    note = make_note("群里回暗号 7788", from_context_ref="dm:1_40", call_count=100)

    assert NOTE_TTL_CALLS == 40
    assert note["expires_at_call"] == 140
    assert is_expired(note, 139) is False and is_expired(note, 140) is True


def test_only_other_conversations_get_it():
    """自己写的不念给自己听"""
    from app.utils.pure.cross_state_note import make_note, deliverable_notes

    note = make_note("暗号 7788", from_context_ref="dm:1_40", call_count=10)

    assert deliverable_notes([note], "group:64", 10, []) == [note]
    assert deliverable_notes([note], "dm:1_40", 10, []) == []


def test_delivered_note_is_not_delivered_twice_and_expires_for_new_ones():
    from app.utils.pure.cross_state_note import make_note, deliverable_notes

    note = make_note("暗号 7788", from_context_ref="dm:1_40", call_count=10)

    assert deliverable_notes([note], "group:64", 11, [note["id"]]) == []
    assert deliverable_notes([note], "group:64", 50, []) == [], "过期就不该再投给别的会话"


def test_frame_notes_block_is_stable_and_actionable():
    """块内容必须每轮字节一致（缓存），且指明长期的东西该去哪儿"""
    from app.utils.pure.cross_state_note import format_frame_notes, note_copy, make_note

    copies = [note_copy(make_note("群里回暗号 7788", from_context_ref="dm:1_40",
                                 from_label="私信「书爱」", call_count=10))]
    first = format_frame_notes(copies)

    assert format_frame_notes(copies) == first, "同样输入必须产出同样字节，否则前缀缓存会断"
    assert "7788" in first and "私信「书爱」" in first
    assert "update_self_config" in first
    assert format_frame_notes([]) == ""


def test_note_body_is_rendered_once_not_by_the_state_summary():
    """状态帧只是便签的抽屉：正文只许前缀块渲染一次，摘要不得再念一遍。

    帧同时供两个渲染器读——前缀块（format_frame_notes）和当前状态摘要
    （format_state_stack_summary）。抽屉里的 notes 一旦被摘要也读出来，
    同一段文字就会在同一份提示词里出现两次。
    """
    from app.utils.pure.cross_state_note import format_frame_notes, note_copy, make_note
    from app.utils.pure.state_stack import format_state_stack_summary

    frame = {
        "id": "f1", "type": "group_chat", "context_ref": "group:64",
        "label": "群「化学老师少宇群」", "doing": "在群里答疑",
        "status": "active",
        "notes": [note_copy(make_note("群里回暗号 7788", from_context_ref="dm:1_40",
                                      from_label="私信「书爱」", call_count=10))],
    }

    block = format_frame_notes(frame["notes"])
    assert block.count("7788") == 1
    assert "7788" not in format_state_stack_summary([frame]), "便签正文被摘要重复渲染了"


def _fake_note_world(notes, calls=12, context_ref="group:64"):
    """装一个假世界（记录 + 一个会话帧），替掉 service 的 DB 依赖。返回 (state, restore)。"""
    from app.services.agent import cross_state_note_service as svc
    from app.services.agent import state_stack_service as sss

    state = {"notes": list(notes), "calls": calls,
             "frame": {"context_ref": context_ref, "notes": []}, "writes": 0}

    async def fake_load(db, agent_id):
        return list(state["notes"]), state["calls"]

    async def fake_list_states(db, agent_id):
        return [state["frame"]]

    async def fake_set_frame_notes(db, agent_id, context_ref, copies):
        state["frame"]["notes"] = copies
        state["writes"] += 1
        return copies

    real = (svc._load, sss.list_states, sss.set_frame_notes)
    svc._load, sss.list_states, sss.set_frame_notes = fake_load, fake_list_states, fake_set_frame_notes

    def restore():
        svc._load, sss.list_states, sss.set_frame_notes = real

    return state, restore


async def test_sync_delivers_once_then_records_lose_all_power_over_it():
    """投递是一次性过户：过户后记录侧（过期 / 剪枝 / 删除 / 清空）都动不了它。

    用户 2026-09-25 的原话：40 次只管「别的会话还能不能拿到它」，拿到了就躺在上下文里、
    归那段会话的锁管——所以 sync 不该再回头跟记录对账。
    """
    from app.services.agent import cross_state_note_service as svc
    from app.utils.pure.cross_state_note import make_note

    note = make_note("群里回暗号 7788", from_context_ref="dm:1_40", call_count=10)
    state, restore = _fake_note_world([note], calls=12)
    try:
        first = await svc.sync_frame_notes(object(), 1, "group:64")
        assert [c["id"] for c in first] == [note["id"]] and state["writes"] == 1

        assert await svc.sync_frame_notes(object(), 1, "group:64") == first
        assert state["writes"] == 1, "已投过就不再碰帧（前缀要字节稳定）"

        state["calls"] = 100  # 记录过期
        assert await svc.sync_frame_notes(object(), 1, "group:64") == first
        state["notes"] = []   # 记录被删掉 / 清空 / 过期剪枝
        assert await svc.sync_frame_notes(object(), 1, "group:64") == first
        assert state["writes"] == 1, "过户完成后记录侧不该再回写帧"
    finally:
        restore()


async def test_sync_delivers_only_live_notes_from_other_conversations():
    """投谁：别人写的 + 还活着的；自己写的不投给自己；栈顶不是本会话时一概不动"""
    from app.services.agent import cross_state_note_service as svc
    from app.utils.pure.cross_state_note import make_note

    mine = make_note("自己的事", from_context_ref="group:64", call_count=10)
    dead = make_note("过期了", from_context_ref="dm:9_9", call_count=0)   # 40 次时过期
    theirs = make_note("群里回暗号 7788", from_context_ref="dm:1_40", call_count=10)
    state, restore = _fake_note_world([mine, dead, theirs], calls=45)
    try:
        got = await svc.sync_frame_notes(object(), 1, "group:64")
        assert [c["id"] for c in got] == [theirs["id"]] and state["writes"] == 1

        state["frame"]["context_ref"] = "dm:1_40"   # 栈顶不是本会话（切换没走完）
        assert await svc.sync_frame_notes(object(), 1, "group:64") == []
    finally:
        restore()


def test_retired_copy_does_not_change_the_prefix_at_all():
    """撤下不许动前缀一个字节：同一份副本标不标 retired，渲染必须一模一样"""
    from app.utils.pure.cross_state_note import format_frame_notes, note_copy, make_note

    live = note_copy(make_note("群里回暗号 7788", from_context_ref="dm:1_40",
                               from_label="私信「书爱」", call_count=10))
    gone = dict(live, retired=True)

    assert format_frame_notes([gone]) == format_frame_notes([live])
    assert "7788" in format_frame_notes([gone]), "前缀里那行照旧活着，靠尾部通知压住它"


def test_retired_notes_notice_rides_in_the_tail():
    """撤下走尾部变更通知：说清是哪条（原文），并声明以后者为准"""
    from app.utils.pure.cross_state_note import format_retired_notes_notice, note_copy, make_note

    live = note_copy(make_note("还没撤的", from_context_ref="dm:1_40", call_count=10))
    gone = note_copy(make_note("群里回暗号 7788", from_context_ref="dm:1_40",
                               from_label="私信「书爱」", call_count=10))
    gone["retired"] = True

    notice = format_retired_notes_notice([live, gone])

    assert "7788" in notice and "私信「书爱」" in notice and "不要再照着执行" in notice
    assert "还没撤的" not in notice, "只通报撤下的那几条"
    assert "以本通知为准" in notice, "前缀里那行还在，必须点明谁说话算数"
    assert format_retired_notes_notice([live]) == "", "没有撤下的就不发通知"

    gone["notified"] = True
    assert format_retired_notes_notice([live, gone]) == "", "发过一次就完事，不再占投递"


async def test_retire_frame_notes_marks_copies_in_every_conversation():
    """删记录 = 把各会话里已投递的副本标成已撤下；不删行，而且不重复写"""
    from app.services.agent import state_stack_service as sss

    stored = {"stack": [
        {"id": "f1", "context_ref": "group:64", "notes": [{"id": "n1"}, {"id": "n2"}]},
        {"id": "f2", "context_ref": "dm:1_40", "notes": [{"id": "n1"}]},
    ]}
    writes: list = []

    async def fake_get_stack(db, agent_id):
        return stored["stack"]

    async def fake_set_stack(db, agent_id, stack):
        writes.append(stack)
        stored["stack"] = stack

    real = (sss._get_stack, sss._set_stack)
    sss._get_stack, sss._set_stack = fake_get_stack, fake_set_stack
    try:
        assert await sss.retire_frame_notes(object(), 1, {"n1"}) == 2
        assert stored["stack"][0]["notes"][0]["retired"] is True
        assert stored["stack"][0]["notes"][1].get("retired") is None, "别的便签不受牵连"
        assert stored["stack"][1]["notes"][0]["retired"] is True
        assert await sss.retire_frame_notes(object(), 1, {"n1"}) == 0, "已经撤过就不再写"
        assert await sss.retire_frame_notes(object(), 1, set()) == 0
        assert len(writes) == 1
    finally:
        sss._get_stack, sss._set_stack = real


async def test_mark_frame_notes_notified_stamps_only_once():
    """撤下通知发一次就盖章，之后同一段对话不再发（不占投递、不重复占位）"""
    from app.services.agent import state_stack_service as sss

    stored = {"stack": [{"id": "f1", "context_ref": "group:64",
                         "notes": [{"id": "n1", "retired": True}, {"id": "n2"}]}]}
    writes: list = []

    async def fake_get_stack(db, agent_id):
        return stored["stack"]

    async def fake_set_stack(db, agent_id, stack):
        writes.append(stack)
        stored["stack"] = stack

    real = (sss._get_stack, sss._set_stack)
    sss._get_stack, sss._set_stack = fake_get_stack, fake_set_stack
    try:
        assert await sss.mark_frame_notes_notified(object(), 1, {"n1"}) == 1
        assert stored["stack"][0]["notes"][0]["notified"] is True
        assert stored["stack"][0]["notes"][1].get("notified") is None, "没撤下的不盖章"
        assert await sss.mark_frame_notes_notified(object(), 1, {"n1"}) == 0
        assert await sss.mark_frame_notes_notified(object(), 1, set()) == 0
        assert len(writes) == 1
    finally:
        sss._get_stack, sss._set_stack = real


async def test_release_active_frame_notes_drops_copies_only_on_unlock():
    """解锁（compact/clear）才丢副本：对话帧清空；AI 手动状态帧不碰"""
    from app.services.agent import state_stack_service as sss

    stored = {"stack": [
        {"id": "f1", "type": "group_chat", "context_ref": "group:64",
         "notes": [{"id": "n1"}, {"id": "n2", "retired": True}]},
    ]}
    writes: list = []

    async def fake_get_stack(db, agent_id):
        return stored["stack"]

    async def fake_set_stack(db, agent_id, stack):
        writes.append(stack)
        stored["stack"] = stack

    real = (sss._get_stack, sss._set_stack)
    sss._get_stack, sss._set_stack = fake_get_stack, fake_set_stack
    try:
        assert await sss.release_active_frame_notes(object(), 1) == 2
        assert stored["stack"][0]["notes"] == []
        assert await sss.release_active_frame_notes(object(), 1) == 0, "已经空的不再写"
        assert len(writes) == 1

        stored["stack"][0]["type"] = "manual"
        stored["stack"][0]["notes"] = [{"id": "n3"}]
        assert await sss.release_active_frame_notes(object(), 1) == 0, "手动状态帧不是会话，别误清"
        assert stored["stack"][0]["notes"] == [{"id": "n3"}] and len(writes) == 1
    finally:
        sss._get_stack, sss._set_stack = real


async def test_remove_note_also_marks_delivered_copies_withdrawn():
    """工具侧删一条便签：记录没了（不再投递）+ 已投出去的那份在会话里标为撤下"""
    from app.services.agent import cross_state_note_service as svc
    from app.services.agent import state_stack_service as sss
    from app.utils.pure.cross_state_note import make_note

    note = make_note("群里回暗号 7788", from_context_ref="dm:1_40", call_count=10)
    saved: dict = {}
    retired: list = []

    async def fake_load(db, agent_id):
        return [note], 12

    async def fake_save(db, agent_id, notes):
        saved["notes"] = list(notes)

    async def fake_retire(db, agent_id, ids):
        retired.append(set(ids))
        return 2

    real = (svc._load, svc._save, sss.retire_frame_notes)
    svc._load, svc._save, sss.retire_frame_notes = fake_load, fake_save, fake_retire
    try:
        kept, msg = await svc.remove_note(object(), 1, note["id"])
        assert kept == [] and saved["notes"] == []
        assert retired == [{note["id"]}] and "2 处会话" in msg, msg

        _, msg = await svc.remove_note(object(), 1, "nope")
        assert "没找到" in msg and len(retired) == 1, "没删到就不该去标撤下"
    finally:
        svc._load, svc._save, sss.retire_frame_notes = real

