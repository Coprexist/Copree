"""@ 提及：身份是 id，名字只是注脚。

起因：群里有人 @ 了别人，AI 收到的正文是 "<@!213> 为什么不跟伟大的大肥鱼老师对话"，
于是回了一句 "咦，@213 是哪位"。名字要在 AI 读的那一份里补上，但**id 必须留在前面**：
AI 靠 id 认人（改名、重名都不会指错），名字只是帮它理解这是谁。
"""
from datetime import datetime

from app.utils.text import render_mention_with_names


class _Msg:
    def __init__(self, content: str, *, mid=4217):
        self.id = mid
        self.content = content
        self.attachments = None
        self.revoked_at = None
        self.sender_type = "human"
        self.sender_id = 90
        self.sender_name = "书爱"
        self.created_at = datetime(2026, 11, 1, 15, 50)


def test_id_comes_first_and_the_name_is_only_an_annotation():
    out = render_mention_with_names("<@!213> 你怎么看", {213: "在命运之前找到我#2"})

    assert out == "<@!213>（在命运之前找到我#2） 你怎么看"


def test_unknown_id_keeps_the_bare_token():
    """查不到名字就只留令牌：不编假名，也绝不换成 @用户N 这种指不准的写法。"""
    assert render_mention_with_names("<@!999> 在吗", {}) == "<@!999> 在吗"


def test_group_entry_annotates_mentions():
    from app.chat.gm import gm_message_entry

    text = gm_message_entry(
        _Msg("<@!213> 为什么不跟伟大的大肥鱼老师对话"),
        agent_name="鲸鱼娘", agent_user_id=120,
        mention_names={213: "在命运之前找到我#2"},
    )["content"]

    assert "<@!213>（在命运之前找到我#2）" in text
    assert "<@!213>（" in text and "为什么不跟伟大的大肥鱼老师对话" in text


def test_group_entry_without_the_map_keeps_the_token_alone():
    """别的调用方（导出、老路径）不传名字表时行为不变。"""
    from app.chat.gm import gm_message_entry

    text = gm_message_entry(_Msg("<@!213> 喂"), agent_name="鲸鱼娘", agent_user_id=120)["content"]

    assert "<@!213> 喂" in text
    assert "<@!213>（" not in text


def test_long_name_is_truncated_to_fifteen_chars():
    out = render_mention_with_names("<@!90> 来", {90: "书爱 Shu Ai Neumann LOVE"})

    assert out == "<@!90>（书爱 Shu Ai Neuma…） 来"


def test_name_of_exactly_fifteen_chars_is_kept():
    name = "一二三四五六七八九十一二三四五"      # 正好 15 字

    assert render_mention_with_names("<@!7> 来", {7: name}) == f"<@!7>（{name}） 来"


def test_truncation_does_not_break_the_token():
    from app.utils.text import iter_mention_ids

    out = render_mention_with_names("<@!8> 来", {8: "一二三四五六七八九十一二三四五六七八"})

    assert iter_mention_ids(out) == [8]
    assert out.startswith("<@!8>（")


def test_plain_text_is_untouched():
    assert render_mention_with_names("就是普通一句话", {}) == "就是普通一句话"


def test_the_annotated_form_stays_a_valid_mention():
    """注脚不能破坏令牌：AI 照抄前半段仍要能被认出是 @ 同一个人。"""
    from app.utils.text import iter_mention_ids

    out = render_mention_with_names("<@!213> 来", {213: "书爱"})

    assert iter_mention_ids(out) == [213]
