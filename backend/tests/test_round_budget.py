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


def test_set_round_budget_never_rewrites_earlier_messages():
    """读数只往末尾长：前面发出去过的一条都不许动（前缀缓存按字节算，改一个字节全废）。

    2026-11-06 线上：原地改写「## 当前时间」那条，轮内工具结果把它挤到中间后，
    每轮把"它之后的所有工具结果"重算一遍——18 轮多花了 25 万 token。
    """
    from app.ai.llm import set_round_budget

    head = {"role": "system", "content": "锁定段"}
    time_block = {"role": "system", "content": "## 当前时间\n2026-09-26 21:12 Asia/Shanghai\n"}
    messages = [head, time_block, {"role": "user", "content": "在吗"}]
    before = [m["content"] for m in messages]

    assert set_round_budget(messages, round_no=1, total=6) is True
    messages.append({"role": "assistant", "content": ""})
    messages.append({"role": "tool", "content": "工具结果"})
    assert set_round_budget(messages, round_no=2, total=6) is True

    assert [m["content"] for m in messages[:3]] == before, "前面已经发出去的那几条被改写了"
    assert "1/6" in messages[3]["content"], "第一轮的读数应在末尾"
    assert "2/6" in messages[-1]["content"], "第二轮的读数要跟在工具结果后面"
    assert time_block["content"] == before[1], "尾部动态块也不再被改写"


def test_set_round_budget_without_messages_returns_false():
    from app.ai.llm import set_round_budget

    assert set_round_budget([], round_no=1, total=6) is False
