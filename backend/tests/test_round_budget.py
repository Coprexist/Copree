"""工具轮次读数：每轮告诉 AI「这是第几轮、还剩几轮」

轮次上限对 AI 原本是隐形的——它会把每一轮都花在检索上，用尽后由平台直接结束，
用户侧只看到「AI 不回了」。读数写在尾部动态块（时间那段），赠送轮必须说明自己不占额度。
"""
import pytest

pytestmark = pytest.mark.anyio


def test_first_round_is_just_a_counter():
    from app.utils.pure.round_budget import round_budget_text

    text = round_budget_text(round_no=1, total=10)

    assert "1/10" in text
    assert "还剩" not in text, "没到末段就别占上下文"


def test_last_three_rounds_ask_to_speak_first():
    from app.utils.pure.round_budget import round_budget_text

    text = round_budget_text(round_no=8, total=10)

    assert "8/10" in text and "还剩 3 轮" in text
    assert "send_gm" in text and "key_note" in text


def test_free_round_says_it_does_not_count():
    from app.utils.pure.round_budget import round_budget_text

    text = round_budget_text(round_no=7, total=6, free=True)

    assert "7/6" in text and "赠送轮" in text and "不占额度" in text


def test_set_round_budget_rewrites_the_same_block():
    """读数写进尾部那块：重复写不能堆成多段，时间与锁定段都不动"""
    from app.ai.llm import set_round_budget
    from app.utils.pure.round_budget import HEADER

    messages = [
        {"role": "system", "content": "锁定段"},
        {"role": "system", "content": "## 当前时间\n2026-09-26 21:12 Asia/Shanghai\n"},
    ]

    assert set_round_budget(messages, round_no=2, total=6) is True
    assert set_round_budget(messages, round_no=3, total=6) is True

    assert messages[0]["content"] == "锁定段"
    assert messages[1]["content"].count(HEADER) == 1, "重复写不能把读数堆成多段"
    assert "3/6" in messages[1]["content"] and "2/6" not in messages[1]["content"]
    assert "## 当前时间" in messages[1]["content"], "时间那段不能被动"


def test_set_round_budget_without_tail_is_a_no_op():
    from app.ai.llm import set_round_budget

    messages = [{"role": "system", "content": "锁定段"}]

    assert set_round_budget(messages, round_no=1, total=6) is False
    assert messages[0]["content"] == "锁定段"
