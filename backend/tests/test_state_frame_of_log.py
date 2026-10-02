"""日志按状态分组：帧身份从注入过的摘要里读回，读错就把两段状态的上下文混成一堆"""

from app.utils.pure.state_stack import (
    make_state_frame, format_state_stack_summary, parse_state_summary, state_frame_of,
)


def _frame(**extras):
    return make_state_frame(type_="group_chat", context_ref="group:66", **extras)


def test_summary_round_trips_back_to_the_frame_identity():
    summary = format_state_stack_summary([_frame(label="群「诗」", doing="在群「诗」中回复书爱")])
    assert parse_state_summary(summary) == {"type": "group_chat", "label": "群「诗」"}


def test_identity_falls_back_to_context_ref_when_label_is_missing():
    assert parse_state_summary(format_state_stack_summary([_frame()])) == {
        "type": "group_chat", "label": "group:66",
    }


def test_paused_top_frame_still_reads():
    """▸（挂起）与 ▸▶（活跃）是同一帧身份，日志不该因此裂成两组"""
    frame = _frame(label="私信「starwee」")
    frame["status"] = "suspended"
    assert parse_state_summary(format_state_stack_summary([frame]))["label"] == "私信「starwee」"


def test_over_long_summary_keeps_the_identity():
    """长度降级只动尾部：首行被截掉的话，越长的状态越查不出来"""
    summary = format_state_stack_summary([_frame(label="群「诗」", doing="干" * 2000)], max_chars=500)
    assert len(summary) > 500
    assert parse_state_summary(summary) == {"type": "group_chat", "label": "群「诗」"}


def test_request_body_reads_the_injected_block():
    body = [
        {"role": "system", "content": "你是……"},
        {"role": "user", "content": "在吗"},
        {"role": "system", "content": format_state_stack_summary([_frame(label="群「诗」")])},
        {"role": "system", "content": "现在时间 ……"},
    ]
    assert state_frame_of(body) == {"type": "group_chat", "label": "群「诗」"}


def test_body_without_state_summary_has_no_state():
    assert state_frame_of([{"role": "system", "content": "你是……"}, {"role": "user", "content": "在吗"}]) == {}
    assert state_frame_of([]) == {}


def test_pasted_text_is_not_mistaken_for_the_state():
    """标记是给人看的一段文字，谁都能抄：只有注入块（system + 首字节就是标记）才算状态"""
    pasted = "▸▶ [dm] (私信「别人」): 抄来的"
    assert state_frame_of([{"role": "user", "content": "## 📋 当前状态\n" + pasted}]) == {}
    assert state_frame_of([{"role": "system", "content": "前面有话 ## 📋 当前状态\n" + pasted}]) == {}
