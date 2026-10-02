"""改变量视图：请求体不是纯追加（中间的注入块每次都变），比错了整屏都是"新增"就白看了"""

from app.services.content.conversation_log_service import _diff_messages


def _sys(text):
    return {"role": "system", "content": text}


def _user(text):
    return {"role": "user", "content": text}


def test_identical_bodies_have_no_change():
    body = [_sys("你是……"), _user("在吗")]
    assert _diff_messages(body, body) == {
        "ops": [{"tag": "equal", "count": 2}],
        "added_count": 0, "removed_count": 0, "shared_count": 2,
    }


def test_appended_tail_is_an_insert_at_the_end():
    prev = [_sys("你是……"), _user("在吗")]
    current = prev + [{"role": "assistant", "tool_calls": [{"id": "1"}]}, {"role": "tool", "content": "{}"}]
    change = _diff_messages(prev, current)
    assert change["ops"] == [
        {"tag": "equal", "count": 2},
        {"tag": "insert", "messages": current[2:]},
    ]
    assert (change["added_count"], change["removed_count"], change["shared_count"]) == (2, 0, 2)


def test_changed_injected_block_is_a_replace_not_a_flood():
    """中间那块相关记忆每次都变：改变量里就该只有它，而不是它后面的全部"""
    prev = [_sys("你是……"), _sys("## 相关记忆\n1. 旧"), _user("在吗"), _user("说话")]
    current = [_sys("你是……"), _sys("## 相关记忆\n1. 新"), _user("在吗"), _user("说话")]
    assert _diff_messages(prev, current)["ops"] == [
        {"tag": "equal", "count": 1},
        {"tag": "replace", "removed": [prev[1]], "added": [current[1]]},
        {"tag": "equal", "count": 2},
    ]


def test_removed_messages_are_kept():
    """回滚 / 上下文被压缩掉的轮次是「消失」，只给新增那一截就看不全了"""
    prev = [_sys("你是……"), _user("旧的一轮"), _user("在吗")]
    current = [_sys("你是……"), _user("在吗")]
    change = _diff_messages(prev, current)
    assert change["ops"] == [
        {"tag": "equal", "count": 1},
        {"tag": "delete", "messages": [prev[1]]},
        {"tag": "equal", "count": 1},
    ]
    assert (change["added_count"], change["removed_count"]) == (0, 1)


def test_real_shaped_body_yields_a_small_change():
    """真机形状：200 来条里改一块注入、尾部再添两条（实测同状态相邻两次调用：新增 9、消失 7、相同 190）"""
    prev = [_sys("你是……"), _sys("## 相关记忆\n1. 旧")] + [_user(f"第 {i} 句") for i in range(196)]
    current = [_sys("你是……"), _sys("## 相关记忆\n1. 新")] + prev[2:] + [
        {"role": "assistant", "tool_calls": [{"id": "1"}]},
        {"role": "tool", "content": "{}"},
    ]
    change = _diff_messages(prev, current)
    assert (change["added_count"], change["removed_count"], change["shared_count"]) == (3, 1, 197)
    assert [op["tag"] for op in change["ops"]] == ["equal", "replace", "equal", "insert"]
    assert change["ops"][1]["removed"] == [prev[1]]
    assert change["ops"][1]["added"] == [current[1]]


def test_first_request_has_everything_as_one_insert():
    change = _diff_messages([], [_sys("你是……")])
    assert change["ops"] == [{"tag": "insert", "messages": [_sys("你是……")]}]
    assert change["shared_count"] == 0
