"""帧的轮生命周期：三态、额度与借贷（docs/dev/frame_turn_lifecycle.md）。

这一层只守纯函数：帧是 JSON 里的一段，测试直接构造，不需要数据库。
额度**只存剩余步数**（steps_left）：判断 / 赋值 / 借还是一行，不会出现 used/limit 不同步。

执行循环接入（每步扣减、跨帧借还）在后续批次，所以这里断言的是**账目本身**。
"""
from app.utils.pure.frame_turn import (
    CLOSED, DEFAULT_STEPS_LIMIT, OPEN, RESERVE_STEPS, RUNNING, STEPS_FIELD,
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
    assert (f["turn_state"], steps_left(f)) == (CLOSED, 0)
    assert STEPS_FIELD in f
    assert "steps_used" not in f and "steps_limit" not in f, "旧字段换算完就丢，只认 steps_left"
    assert f["borrow_from"] == []


def test_old_frames_are_converted_not_zeroed():
    """重启时旧帧按「上限 - 已用」换算，**不能一律清零**——正在跑的帧会被清成 0 直接卡死。"""
    f = ensure_fields(_frame(steps_used=7, steps_limit=20))
    assert steps_left(f) == 13


def test_new_stimulus_resets_quota_and_clears_debts():
    """刺激到达：额度重置、标 running（常态下接着就开跑）。

    但「挂着等窗口」的帧必须由挂起方 suspend 回 open——不然它和正在跑的长得一样，
    attach_stimulus 判「忙」时会把等着的那一帧也算进去（2026-11-26 修轮末归位时发现）。
    """
    f = _frame(turn_state=OPEN, steps_left=5, lent_out=3,
               borrow_from=[{"frame": "p", "n": 2}])
    new_stimulus(f, limit=8)
    assert f["turn_state"] == RUNNING
    assert steps_left(f) == 8
    assert f["lent_out"] == 0 and f["borrow_from"] == []


def test_new_stimulus_never_sets_zero_limit():
    f = _frame()
    new_stimulus(f, limit=0)
    assert steps_left(f) == DEFAULT_STEPS_LIMIT


def test_suspend_keeps_quota_and_resume_does_not_reset_it():
    """形态 1：走开一会儿回来继续——额度只被刺激重置，切帧不动。"""
    f = _frame(turn_state=RUNNING, steps_left=13)
    suspend(f)
    assert f["turn_state"] == OPEN and steps_left(f) == 13
    resume(f)
    assert f["turn_state"] == RUNNING and steps_left(f) == 13


def test_close_reclaims_the_remaining_quota():
    """§4.3：弹出时剩余预算清零——不能留给下一次进入（那就成了续命）。"""
    f = _frame(turn_state=RUNNING, steps_left=13)
    close(f)
    assert f["turn_state"] == CLOSED
    assert steps_left(f) == 0
    assert f["status"] == "ended", "close 必须同时出链，否则它一直占着 active（线上踩过）"


def test_borrow_leaves_one_step_for_the_parent():
    """I3：父帧至少留 1 步给自己收尾；借出去的额度只转移、不新增（I2）。"""
    parent = _frame(id="p", turn_state=OPEN, steps_left=2)
    child = _frame(id="c")
    take = borrow(5, parent)
    assert take == 1, "只剩 2 步时最多借 1 步（留 1）"
    record_borrow(child, parent, take)
    assert parent["lent_out"] == 1
    assert child["borrow_from"] == [{"frame": "p", "n": 1}]
    assert steps_left(parent) == RESERVE_STEPS


def test_borrow_refused_when_parent_has_only_the_reserve():
    parent = _frame(id="p", turn_state=OPEN, steps_left=1)
    assert borrow(3, parent) == 0
    assert steps_left(parent) == 1, "不能把父帧的收尾步也借走"


def test_borrow_never_goes_into_debt():
    parent = _frame(id="p", turn_state=OPEN, steps_left=4)
    assert borrow(10, parent) == 3
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
    """§7.1：注入必须带「在哪、还剩几步、切走的后果」——模型默认消息都是实时的。"""
    note = status_note(_frame(label="群「甲群」", turn_state=RUNNING, steps_left=15))
    assert "群「甲群」" in note
    assert "还剩 15 步" in note
    assert "open" in note and "额度保留" in note
    assert "尚未发出消息" in note


def test_status_note_says_sent_when_it_has():
    note = status_note(_frame(label="dm:1_2", steps_left=2), has_sent=True)
    assert "已经发过消息" in note
