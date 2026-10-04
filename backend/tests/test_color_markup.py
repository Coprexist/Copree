"""站内彩色语法：出站怎么变（QQ 的 Markdown 没有颜色 → 加粗；纯文本 → 只脱标签）

文法只有一处（utils/text.COLOR_TAGS）：前端渲染、工具描述、出站降级都从它走。
"""
from app.utils.text import (
    COLOR_SYNTAX_HINT, COLOR_TAGS, plainify_markdown, render_color_markup, strip_color_markup,
)


def test_both_syntaxes_strip_to_plain_text():
    assert strip_color_markup("[blue]蓝[/blue] 尾") == "蓝 尾"
    assert strip_color_markup('<span class="text-red">红</span>尾') == "红尾"
    assert strip_color_markup("[gold]金[/gold][gold]二[/gold]") == "金二"


def test_markdown_path_degrades_color_to_bold():
    """QQ 支持 Markdown 但不支持彩色：降级成加粗（比整段褪成黑字强）"""
    assert render_color_markup("[blue]蓝[/blue] 与 <span class=\"text-red\">红</span>", markdown=True) \
        == "**蓝** 与 **红**"
    # 支持彩色的通道不调它：原样发就是彩色，这条只钉住"调了才降级"
    assert render_color_markup("[blue]蓝[/blue]", markdown=False) == "蓝"
    # 空内容不留一对空星号
    assert render_color_markup("[blue][/blue]x", markdown=True) == "x"


def test_plain_text_path_strips_instead_of_showing_stars():
    assert plainify_markdown("[blue]蓝[/blue] **粗**") == "蓝 粗"


def test_plain_brackets_are_left_alone():
    """[1,2]、未闭合的 [blue] 都是正文，别当成我们的语法吃掉"""
    assert strip_color_markup("数组 [1,2] 与 [blue]未闭合") == "数组 [1,2] 与 [blue]未闭合"
    assert render_color_markup("普通 [note] 标签", markdown=True) == "普通 [note] 标签"


def test_tool_descriptions_share_the_one_color_table():
    """三个发送工具的描述都由 COLOR_TAGS 生成：色表加一个，提示自动跟上"""
    from app.tools.chat_social.send_dm import SendDM
    from app.tools.chat_social.send_file import SendFile
    from app.tools.chat_social.send_message import SendGm

    for tool in (SendGm, SendDM, SendFile):
        assert COLOR_SYNTAX_HINT in tool.parameters["content"]["description"]
    for name, zh in COLOR_TAGS:
        assert f"[{name}]{zh}[/{name}]" in COLOR_SYNTAX_HINT
