"""file_fetch 的选区规则（services/content/attachment_source）

工具层不写这些判断，就是为了能在这里逐条钉死：编号怎么算、缺号怎么报、
不传参数收几个。它们错了，AI 就会「以为收了第二张、其实收的是第一张」。
"""
from app.services.content.attachment_source import select_for_fetch

A = {"file_id": 11, "path": "a.png", "name": "a.png", "mime_type": "image/png"}
B = {"file_id": 12, "path": "b.png", "name": "b.png", "mime_type": "image/png"}
C = {"file_id": 13, "path": "c.txt", "name": "c.txt", "mime_type": "text/plain"}
ATT = [A, B, C]


def test_no_argument_takes_every_attachment():
    """不传 = 收整条消息：旧实现只取第一个，那是漏收不是约定。"""
    sel = select_for_fetch(ATT)
    assert sel.file_ids == (11, 12, 13)
    assert sel.missing_indexes == ()
    assert sel.is_empty is False


def test_indexes_follow_the_order_the_model_gave():
    sel = select_for_fetch(ATT, indexes=[3, 1])
    assert sel.file_ids == (13, 11), "按模型给的顺序收，不替它重排"


def test_missing_index_is_reported_with_what_is_available():
    sel = select_for_fetch(ATT, indexes=[2, 9])
    assert sel.file_ids == (12,)
    assert sel.missing_indexes == (9,)
    assert sel.available_indexes == (1, 2, 3), "报错要说清这条消息有哪些号"


def test_explicit_file_ids_pass_through():
    sel = select_for_fetch(ATT, file_ids=[12])
    assert sel.file_ids == (12,)


def test_scalar_is_tolerated():
    """模型偶尔把 [2] 写成 2：宽容一个单值，别让它整次调用失败。"""
    assert select_for_fetch(ATT, indexes=2).file_ids == (12,)


def test_cards_do_not_take_a_number():
    """卡片类附件（好友/群邀请）没有文件语义，不占编号——否则会和标记层错位。"""
    cards = [{"type": "friend_request", "id": 99}, A]
    sel = select_for_fetch(cards, indexes=[1])
    assert sel.file_ids == (11,)


def test_empty_message_selects_nothing():
    sel = select_for_fetch([])
    assert sel.is_empty is True
    assert sel.file_ids == ()
