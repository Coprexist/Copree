"""一轮对话的结局判定：只看这一轮，别被账本里的历史失败带跑"""
import pytest

pytestmark = pytest.mark.anyio


def test_history_failures_do_not_turn_this_turn_into_error():
    """上一轮的工具失败不能把这一轮标红——账本条目会长住上下文（线上 687 条的会话）"""
    from app.services.content.conversation_log_service import _run_status

    messages = [
        {"role": "user", "content": "早"},
        {"role": "assistant", "content": "[本轮工具] handle_friend_request(失败：执行失败：xxx)"},
        {"role": "tool", "content": '{"stderr": "Traceback (most recent call last): ..."}'},
        {"role": "user", "content": "在吗"},       # ← 本轮从这里开始
        {"role": "assistant", "content": "在的"},
    ]
    assert _run_status(messages, True) == "ok"


def test_this_turn_failure_and_wrapup_are_still_reported():
    """本轮自己的失败、收尾、无输出照旧分得开"""
    from app.services.content.conversation_log_service import _run_status

    assert _run_status([
        {"role": "user", "content": "跑个脚本"},
        {"role": "tool", "content": '{"stderr": "Traceback (most recent call last): ..."}'},
    ], False) == "error"

    assert _run_status([{"role": "user", "content": "在吗"},
                        {"role": "assistant", "content": "[本轮收尾] 工具轮次用完了"}], False) == "wrapup"

    assert _run_status([{"role": "user", "content": "在吗"}], False) == "no_output"