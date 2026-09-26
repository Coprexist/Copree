"""@ 提及的 id 化契约（2026-09-25 用户定：平台内的 @ 也一律用 id，写法与 QQ 的 <@!openid> 同形）。

为什么必须 id：名字会改、会重名，QQ 昵称还带空格（「@书爱 Shu Ai Neumann ᴸᴼᵛᴱ」切不干净）。
入口（人的手打、QQ 入站、工具调用、世界桥都会经过 send_gm_message）把 @名字 归一成 <@!id> 一次，
之后唤醒判定、AI 上下文、通道出口就都不必再各写一套名字比对。

兼容：识别同时认 <@!id> 与旧的 @名字（历史消息、还没走入口的老客户端），旧消息不做迁移。
"""
from app.utils.text import (
    check_mention,
    iter_mention_ids,
    link_mentions,
    mention_token,
    mentions_user,
)

NAMES = {"小明": 7, "化学老师": 24, "书爱 Shu Ai Neumann ᴸᴼᵛᴱ": 40}


def test_token_is_the_qq_shape():
    """<@!id>：与 QQ 的内联 @ 同形，通道出口只要把壳里的 id 换成 openid 就能发成真 @。"""
    assert mention_token(41) == "<@!41>"
    assert iter_mention_ids("你好 <@!41> 和 <@!7>，还有 <@!41>") == [41, 7]
    assert mentions_user("<@!41> 在吗", 41) is True
    assert mentions_user("<@!41> 在吗", 7) is False


def test_entry_normalizes_names_to_ids():
    assert link_mentions("@小明 你好", NAMES) == "<@!7> 你好"
    # QQ 昵称带空格：整串要认；AI 往往只写第一个词，也要认
    assert link_mentions("@书爱 Shu Ai Neumann ᴸᴼᵛᴱ 你好", NAMES) == "<@!40> 你好"
    assert link_mentions("@书爱 你好", NAMES) == "<@!40> 你好"


def test_entry_respects_the_shared_boundary_rules():
    """短名不能把长名切一半（与 extract_mentions/check_mention 共用同一份字符集）。"""
    assert link_mentions("@化学老师·少宇 你好", NAMES) == "@化学老师·少宇 你好"
    assert link_mentions("@小明明 你好", NAMES) == "@小明明 你好"
    assert link_mentions("@路人甲 你好", NAMES) == "@路人甲 你好"


def test_ambiguous_first_words_are_left_alone():
    """两个人的昵称第一个词一样 → 只写第一个词时弃用：宁可留原文，也别 @ 错人（全名照常认）。"""
    names = {"书爱 Shu Ai": 40, "书爱 Ai": 41}
    assert link_mentions("@书爱 你好", names) == "@书爱 你好"
    assert link_mentions("@书爱 Shu Ai 你好", names) == "<@!40> 你好"
    assert link_mentions("@书爱 Ai 你好", names) == "<@!41> 你好"


def test_entry_is_idempotent_and_cheap():
    once = link_mentions("@小明 和 @化学老师", NAMES)
    assert once == "<@!7> 和 <@!24>"
    assert link_mentions(once, NAMES) == once          # 已经是令牌的正文再跑一遍不变
    assert link_mentions("没有 @ 的正文", NAMES) == "没有 @ 的正文"
    assert link_mentions("", NAMES) == ""


def test_outbound_render_mentions():
    """出口：<@!id> 换成通道侧写法（QQ 官方 <@!openid>、NapCat 的 CQ 码）。"""
    from app.utils.text import render_mentions

    assert render_mentions("<@!40> 你好", lambda uid: f"<@!OPENID-{uid}>") == "<@!OPENID-40> 你好"
    assert render_mentions("<@!40> 你好", lambda uid: f"[CQ:at,qq={uid}]") == "[CQ:at,qq=40] 你好"
    assert render_mentions("没有令牌的正文", lambda uid: "x") == "没有令牌的正文"


def test_strip_leading_mention_accepts_the_token():
    """摘开头的 @ 也要认新写法：QQ 被动回复自带 @，正文里再来一个就成了两个 @。"""
    from app.utils.text import strip_leading_mention

    assert strip_leading_mention("<@!40> 你好", "书爱", 40) == "你好"
    assert strip_leading_mention("<@!40>，你好", "书爱", 40) == "你好"
    assert strip_leading_mention("你好 <@!40>", "书爱", 40) == "你好 <@!40>"   # 只摘开头
    assert strip_leading_mention("<@!40> 你好", "", 40) == "你好"             # 名字空也摘得掉
    assert strip_leading_mention("<@!40> 你好", "书爱", 7) == "<@!40> 你好"    # 别人不摘


def test_check_mention_accepts_both_writings():
    """新写法认、旧写法也认：不兼容旧的就会叫不醒人（世界群是 mention_only）。"""
    assert check_mention("<@!41> 你好", "浮生", 41) is True
    assert check_mention("<@!41> 你好", "浮生", 7) is False
    assert check_mention("@浮生 你好", "浮生", 41) is True        # 旧写法（历史消息/人的手打）
    assert check_mention("<@!41> 你好", "浮生") is False          # 不给 id 就只按名字认
    assert check_mention("<@!41> 你好", "", 41) is True           # 名字为空也认得出
