"""@ 提及识别契约（用户 2026-09-23：世界群里 @ 了 AI 也不醒）。

真因：_MENTION_RE 把 （）()【】 等当分隔符，名字里带括号的 AI（「浮生（人物志1）」）
只能被提取成「浮生」，check_mention 拿全名去比永远 False；而世界群的触发模式是
mention_only —— @ 不上就等于这个 AI 永远不醒（群 59「CoExisten」/ 群 58「群世界测试」）。

修法是按**整串**再找一次，但必须是**全字匹配**：括号不是可选后缀，@短名不认，
反过来「@浮生（人物志1）」也不该唤醒只叫「浮生」的 AI（用户 2026-09-23 明确要求）。
"""
from app.utils.text import check_mention, extract_mentions, plainify_markdown, strip_leading_mention


def test_plainify_markdown_keeps_text_drops_markers():
    """没开通 MD 权限的机器人退到纯文本时：标记洗掉，正文一个字不少。"""
    src = "# 氯气 Cl2\n\n**一、物理性质**\n- 黄绿色\n- 有毒\n\n[官方网站](https://example.com)\n~~删掉~~\n> 引用\n---\n```py\nprint(1)\n```\n行内 `Cl2` 也要洗"
    out = plainify_markdown(src)
    assert "**" not in out and "#" not in out and "~~" not in out and ">" not in out
    assert "`" not in out, out
    for keep in ("氯气 Cl2", "一、物理性质", "黄绿色", "官方网站（https://example.com）", "删掉", "引用", "print(1)", "行内 Cl2 也要洗"):
        assert keep in out, keep
    assert "- 黄绿色" in out, "列表符号留着，纯文本里也有用"
    assert plainify_markdown("") == ""


def test_plainify_markdown_does_not_maul_code_or_tokens():
    """真机踩点：这个函数原先只服务 QQ 纯文本降级，站内浮窗接上之前先把它钉死

    三件事：@令牌与 CQ 码原样留着（不是 CQ 解析器）、词内下划线不当强调、
    图片没有说明文字时给占位（否则纯图片消息会变成一条空白正文）。
    """
    # @令牌与 CQ 码：原样传过。浮窗那条链路是先 render_mention_names 再进这里，
    # 所以令牌本来就已经换成名字了；这里钉的是"它不会把令牌/CQ 当 Markdown 吃掉"
    assert plainify_markdown("你好 <@!12> 看下") == "你好 <@!12> 看下"
    cq = "看这个 [CQ:image,file=a.jpg] 和 [CQ:at,qq=123]"
    assert plainify_markdown(cq) == cq

    # 词内下划线：AI 写的标识符不能被当成斜体吃掉
    assert plainify_markdown("函数 file_read 与 a_b_c") == "函数 file_read 与 a_b_c"
    assert plainify_markdown("snake_case_name") == "snake_case_name"
    # 正常的下划线强调照旧洗掉
    assert plainify_markdown("_斜体_ 和 __粗体__") == "斜体 和 粗体"

    # 图片：有说明留说明，没有就给占位
    assert plainify_markdown("![示意图](https://x/a.png)") == "示意图"
    assert plainify_markdown("![](https://x/a.png)") == "[图片]"

    # 链接：默认保留地址（QQ 纯文本降级要它）；浮窗传 keep_url=False 只留文字
    assert plainify_markdown("见 [文档](https://x/y)") == "见 文档（https://x/y）"
    assert plainify_markdown("见 [文档](https://x/y)", keep_url=False) == "见 文档"


def test_strip_leading_mention_only_for_that_person():
    """出站摘 @：只摘开头、只摘对那个人的；正文中间的一律不动。"""
    assert strip_leading_mention("@小明 今天天气不错", "小明") == "今天天气不错"
    assert strip_leading_mention("@小明，今天天气不错", "小明") == "今天天气不错"
    assert strip_leading_mention("今天天气不错 @小明", "小明") == "今天天气不错 @小明"
    # QQ 昵称里有空格，要整串匹配得上（用户那个昵称就是 "书爱 Shu Ai Neumann ᴸᴼᵛᴱ"）
    assert strip_leading_mention("@书爱 Shu Ai Neumann 你好", "书爱 Shu Ai Neumann") == "你好"
    # 短名不能把长名切一半（左括号不是边界，全字匹配）
    assert strip_leading_mention("@小明（人物志1） 你好", "小明") == "@小明（人物志1） 你好"
    assert strip_leading_mention("@小明（人物志1） 你好", "小明（人物志1）") == "你好"
    assert strip_leading_mention("@小明 你好", "") == "@小明 你好"
    # QQ 昵称很长时 AI 往往只写第一个词，也要摘掉（否则 QQ 侧又成两个 @）
    assert strip_leading_mention("@书爱 你好", "书爱 Shu Ai Neumann ᴸᴼᵛᴱ") == "你好"
    assert strip_leading_mention("@在命运 你好", "书爱 Shu Ai") == "@在命运 你好"


def test_name_with_parens_matches_as_a_whole():
    assert check_mention("@浮生（人物志1） 你好", "浮生（人物志1）") is True
    assert check_mention("大家都来 @浮生（人物志1） 看看", "浮生（人物志1）") is True
    assert check_mention("@浮生（人物志1）", "浮生（人物志1）") is True


def test_short_name_does_not_wake_the_long_name_one():
    """括号不是可选后缀：@短名 不认（全字匹配）。"""
    assert check_mention("@浮生 你好", "浮生（人物志1）") is False
    assert check_mention("@浮生(人物志1) 你好", "浮生（人物志1）") is False


def test_long_name_does_not_wake_the_short_name_one():
    """反过来也不认：只叫「浮生」的 AI 不该被「@浮生（人物志1）」唤醒。"""
    assert check_mention("@浮生（人物志1） 你好", "浮生") is False
    assert check_mention("@浮生 你好", "浮生") is True


def test_plain_names_unchanged():
    assert check_mention("你好 @梦希 在吗", "梦希") is True
    assert check_mention("你好 @梦希 在吗", "涵吾珑") is False


def test_no_at_sign_is_not_a_mention():
    assert check_mention("浮生（人物志1）你好", "浮生（人物志1）") is False
    assert check_mention("@别人 你好", "浮生（人物志1）") is False
    assert check_mention("", "浮生（人物志1）") is False


def test_at_all_and_at_ai_still_work():
    assert check_mention("@all 集合", "浮生（人物志1）") is True
    assert check_mention("@ai 帮我看下", "浮生（人物志1）") is True
    assert check_mention("@全体 开会", "浮生（人物志1）") is False  # 只有 @all/@ai 是通配


def test_extract_mentions_still_truncates_at_punctuation():
    """提取规则本身不动（它是给别处用的），只是 check_mention 不再只依赖它。"""
    assert extract_mentions("@浮生（人物志1） 你好") == {"浮生"}
