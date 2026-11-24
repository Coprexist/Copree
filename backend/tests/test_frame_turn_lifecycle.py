"""帧的轮生命周期：三态、额度与借贷（docs/dev/frame_turn_lifecycle.md）。

这一层只守纯函数：帧是 JSON 里的一段，测试直接构造，不需要数据库。
执行循环接入（每步扣减、跨帧借还）在后续批次，所以这里断言的是**账目本身**。
"""
from app.utils.pure.frame_turn import (
    CLOSED, DEFAULT_STEPS_LIMIT, OPEN, RESERVE_STEPS, RUNNING,
    borrow, close, ensure_fields, new_stimulus, record_borrow, resume,
    status_note, steps_left, suspend, validate_regression,
)


def _frame(**kw):
    base = {"id": "f1", "type": "group_chat", "context_ref": "group:12"}
    base.update(kw)
    return base


def test_missing_fields_read_as_closed_and_zero():
    """老帧没有这些列：读时补默认，不让新代码在旧快照上 KeyError。"""
    f = ensure_fields(_frame())
    assert (f["turn_state"], f["steps_used"], f["steps_limit"]) == (CLOSED, 0, 0)
    assert f["borrow_from"] == []
    assert steps_left(f) == 0


def test_new_stimulus_resets_quota_and_clears_debts():
    f = _frame(turn_state=OPEN, steps_used=15, steps_limit=20, lent_out=3,
               borrow_from=[{"frame": "p", "n": 2}])
    new_stimulus(f, limit=8)
    assert f["turn_state"] == RUNNING
    assert (f["steps_used"], f["steps_limit"]) == (0, 8)
    assert f["lent_out"] == 0 and f["borrow_from"] == []


def test_new_stimulus_never_sets_zero_limit():
    f = _frame()
    new_stimulus(f, limit=0)
    assert f["steps_limit"] == DEFAULT_STEPS_LIMIT


def test_suspend_keeps_quota_and_resume_does_not_reset_it():
    """形态 1：走开一会儿回来继续——额度只被刺激重置，切帧不动。"""
    f = _frame(turn_state=RUNNING, steps_used=7, steps_limit=20)
    suspend(f)
    assert f["turn_state"] == OPEN and f["steps_used"] == 7
    resume(f)
    assert f["turn_state"] == RUNNING and f["steps_used"] == 7
    assert steps_left(f) == 13


def test_close_reclaims_the_remaining_quota():
    """§4.3：弹出时剩余预算清零——不能留给下一次进入（那就成了续命）。"""
    f = _frame(turn_state=RUNNING, steps_used=7, steps_limit=20)
    close(f)
    assert f["turn_state"] == CLOSED
    assert steps_left(f) == 0


def test_borrow_leaves_one_step_for_the_parent():
    """I3：父帧至少留 1 步给自己收尾；借出去的额度只转移、不新增（I2）。"""
    parent = _frame(id="p", turn_state=OPEN, steps_used=18, steps_limit=20)
    child = _frame(id="c")
    take = borrow(5, parent)
    assert take == 1, "只剩 2 步时最多借 1 步（留 1）"
    record_borrow(child, parent, take)
    assert parent["steps_used"] == 19 and parent["lent_out"] == 1
    assert child["borrow_from"] == [{"frame": "p", "n": 1}]
    # I2：两边 steps_left 之和不变（借 1 步 = 父少 1、子多 1）
    assert steps_left(parent) == 1


def test_borrow_refused_when_parent_has_only_the_reserve():
    parent = _frame(id="p", turn_state=OPEN, steps_used=19, steps_limit=20)
    assert borrow(3, parent) == 0
    assert parent["steps_used"] == 19, "不能把父帧的收尾步也借走"


def test_borrow_never_goes_into_debt():
    parent = _frame(id="p", turn_state=OPEN, steps_used=0, steps_limit=4)
    assert borrow(10, parent) == 3
    assert parent["steps_used"] == 3
    assert steps_left(parent) == RESERVE_STEPS


def test_only_three_regression_shapes_are_legal():
    """§5.3：6 种组合里只剩三种语义成立；其余在入口拦住。"""
    legal = {
        ("suspend", "keep"): True,
        ("end", "keep"): True,
        ("end", "end"): True,
        ("suspend", "end"): False,
        ("end", "same"): False,
        ("none", "keep"): False,
    }
    for (leave, middle), expect in legal.items():
        assert validate_regression(leave=leave, middle=middle, enter=True) is expect, (leave, middle)
    assert validate_regression(leave="end", middle="keep", enter=False) is False


def test_status_note_reports_scope_steps_and_consequence():
    """§7.1：注入必须带「在哪、第几步、切走的后果」——模型默认消息都是实时的。"""
    note = status_note(_frame(label="群「甲群」", turn_state=RUNNING,
                              steps_used=5, steps_limit=20))
    assert "群「甲群」" in note
    assert "5/20" in note
    assert "open" in note and "额度保留" in note
    assert "尚未发出消息" in note


def test_status_note_says_sent_when_it_has():
    note = status_note(_frame(label="dm:1_2", steps_used=1, steps_limit=3), has_sent=True)
    assert "已经发过消息" in note
