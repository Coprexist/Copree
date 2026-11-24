"""文字直发与单轮发言闸（纯函数，零网络）。

2026-11-24 实测出的两个问题，都不是「直接发」引入的，但都在这里补上：

1. **回声**：模型把上下文标记原样复述——`[Shanghai 11-24 21:26] ShuAICFR（id=1）:
   可以用中文吗 [msg_id=5700]`。原来静默留账天然不发，一开直发就把它当回复发出去了。
2. **刷屏**：一个 turn 里 `send_dm` 连发 22 条、来回重复两句话。平台只有「工具轮次」
   上限（帧预算默认 50），没有「一个 turn 能说几句」的闸；云端模型说一句就 end_turn，
   所以这个缺口一直没暴露，4B 模型不会 end_turn 就刷屏。
"""


def test_echo_marker_is_rejected():
    from app.ai.executor import plain_text_reject_reason

    echoed = "[Shanghai 11-24 21:26] ShuAICFR（id=1）: 可以用中文吗 [msg_id=5700]"
    assert plain_text_reject_reason(echoed), "复述上下文标记的不能发"
    assert plain_text_reject_reason("看这条 [msg_id=42]")
    assert plain_text_reject_reason("ShuAICFR（id=1）说的对")
    assert plain_text_reject_reason("[Beijing 11-24 09:00] 某人: 早")


def test_normal_reply_is_allowed():
    from app.ai.executor import plain_text_reject_reason

    for text in ("在。", "可以用中文，我以后都用中文。", "今天下雨，我在看书。"):
        assert plain_text_reject_reason(text) == "", text


def test_empty_reply_is_rejected():
    from app.ai.executor import plain_text_reject_reason

    assert plain_text_reject_reason("")
    assert plain_text_reject_reason("   ")
    assert plain_text_reject_reason("。")


def test_send_cap_is_a_separate_limit():
    """发言条数必须与「工具轮次」分家：轮次够多就能刷屏，实测 22 条。"""
    from app.ai.executor import _MAX_SENDS_PER_TURN, _SEND_TOOLS

    assert _MAX_SENDS_PER_TURN <= 5, "上限要能防刷屏"
    assert set(_SEND_TOOLS) == {"send_gm", "send_dm", "send_voice"}