"""长消息折叠契约。

起因：AI 自己的回复被截在 256 字，"你 60 多分"落在 400 字之后 → 它那一轮**真的没看见**，
于是反过来否认自己说过这话（线上实测，账本条目里连这三个字都没有）。只留开头会让它对自己的话失忆。
"""
from app.utils.pure.history import FOLD_HEAD_RATIO, fold_text


def test_short_text_untouched():
    assert fold_text("短消息", limit=2048) == "短消息"
    assert fold_text("正好" * 1024, limit=2048) == "正好" * 1024      # 正好等于上限不折
    assert fold_text("随便什么", limit=0) == "随便什么"                # 0 = 不折叠


def test_long_text_keeps_head_and_tail():
    body = "头" * 1000 + "丢" * 1500 + "尾" * 800                      # 3300 字
    out = fold_text(body, limit=2048, expand_id=1359)
    head_n = int(2048 * FOLD_HEAD_RATIO)
    assert out.startswith(body[:head_n]), "前 75% 要原样给"
    assert out.endswith(body[-(2048 - head_n):]), "后 25% 也要给（结论/追问常在最后一段）"
    assert f"中间省略 {len(body) - 2048} 字" in out
    assert "[展开 id=1359]" in out
    omitted = body[head_n: len(body) - (2048 - head_n)]
    assert omitted and omitted not in out, "省略窗口那段确实没给"


def test_expand_id_is_optional():
    out = fold_text("x" * 3000)
    assert "省略" in out and "[展开 id=" not in out


def test_group_entry_folds_with_the_message_id():
    """群聊条目：折叠 + 带上这条消息的 id——AI 才有办法用 expand_message 展开。"""
    from datetime import datetime

    from app.chat.gm import gm_message_entry

    class _Msg:
        id = 1359
        content = "头" * 1500 + "丢" * 1500 + "尾" * 1000
        sender_type = "ai"
        sender_id = 2
        sender_name = None
        created_at = datetime(2026, 9, 26, 7, 30)

    text = gm_message_entry(_Msg(), agent_name="化学老师", agent_user_id=2)["content"]
    assert "尾" * 50 in text and "省略" in text and "[展开 id=1359]" in text
    assert "丢" * 50 not in text
