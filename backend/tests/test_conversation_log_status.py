"""一轮对话的结局判定：列表按它分组，判错了就等于把要修的轮次藏进正常里"""

from app.services.content.conversation_log_service import _run_status


def test_status_reports_error_and_wrapup():
    assert _run_status([{"role": "assistant", "content": "[本轮收尾] 本轮因异常中断，未产出回复"}], True) == "error"
    assert _run_status([{"role": "tool", "content": "Traceback (most recent call last): ..."}], True) == "error"
    assert _run_status([{"role": "assistant", "content": "[本轮收尾] 工具轮次用尽，已收尾"}], True) == "wrapup"


def test_status_reports_no_output_and_ok():
    assert _run_status([{"role": "assistant", "content": "[本轮工具] end_turn(ok)"}], False) == "no_output"
    assert _run_status([], False) == "no_output"
    assert _run_status([{"role": "assistant", "content": "在的"}], True) == "ok"


def test_status_only_reads_the_ai_side():
    """判据只看 AI 侧留下的字：群里的人说"你执行失败了吧"不算这一轮报错"""
    assert _run_status([
        {"role": "user", "content": "你这轮是不是执行失败了"},
        {"role": "assistant", "content": "没有，正常回复"},
    ], True) == "ok"
