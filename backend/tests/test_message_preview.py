"""会话列表/站内浮窗那一行预览的口径。

预览是"顺手给人看"的一版：@ 令牌换名字、Markdown 与彩色标签脱掉、附件给占位、
撤回只留占位。这条口径只允许有一份（text_preview），否则就会出现列表里露着
[blue] 字面量、浮窗里却是干净的这种对不齐。
"""
from datetime import datetime

from app.utils.message_serializer import REVOKED_PREVIEW, preview_of, text_preview

VOICE = [{"file_id": 219, "name": "voice_20261114104640_1.mp3", "mime_type": "audio/mpeg"}]


def test_color_tags_are_stripped_from_preview():
    """2026-11-14 用户实测：发 [blue]123[/blue]，侧栏显示的是标签字面量"""
    assert text_preview("[blue]123[/blue]") == "123"
    assert text_preview("[gold]金色[/gold]与[red]红色[/red]") == "金色与红色"
    # HTML 写法也要认（与 colorize 渲染的两种写法对齐）
    assert text_preview('<span class="text-red">热</span>') == "热"


def test_markdown_symbols_do_not_leak_into_preview():
    assert text_preview("**重点**和`code`") == "重点和code"
    assert text_preview("# 标题") == "标题"


def test_voice_attachment_previews_as_placeholder():
    """语音给 [语音]：文件名在列表里既长又没信息"""
    assert text_preview("", VOICE) == "[语音]"
    assert text_preview("", [{"name": "a.png", "mime_type": "image/png"}]) == "[图片]"


def test_voice_preview_carries_duration():
    """有时长就写进去（快照里的 duration_ms，缺了只退回 [语音]，不显示 0'00"）"""
    def voice(ms):
        return [{**VOICE[0], "duration_ms": ms}]

    assert text_preview("", voice(42_000)) == '[语音 42"]'
    assert text_preview("", voice(102_000)) == "[语音 1'42]"
    assert text_preview("", voice(12_400)) == '[语音 12"]'
    assert text_preview("", voice(None)) == "[语音]"


def test_revoked_preview_says_who_recalled():
    """撤回那行一律写名字（私信也一样，不分"你/对方"）：完整名字，不截前几个字"""
    from app.utils.message_serializer import REVOKED_PREVIEW, revoked_preview

    assert revoked_preview("化学老师") == "化学老师 撤回了一条消息"
    assert revoked_preview("一个特别特别长的名字") == "一个特别特别长的名字 撤回了一条消息"
    # 拿不到名字：退回中性说法，不猜
    assert revoked_preview() == REVOKED_PREVIEW
    assert revoked_preview(None) == REVOKED_PREVIEW


def test_revoked_message_previews_as_placeholder():
    class _Msg:
        content = "222"
        attachments = None
        revoked_at = datetime(2026, 11, 14, 10, 55)

    assert preview_of(_Msg(), {}) == REVOKED_PREVIEW
    assert "222" not in REVOKED_PREVIEW

    class _Alive(_Msg):
        revoked_at = None

    assert preview_of(_Alive(), {}) == "222", "没撤回的照常显示正文"
