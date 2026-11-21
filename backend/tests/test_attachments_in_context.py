"""附件在上下文里的呈现：一条消息发了个文件，AI 得知道；文本附件还得读得到内容。

起因：主站账号在群里直接带 txt 发消息，附件落在 messages.attachments 里，
而群聊的渲染（gm_message_entry）只读 content —— AI 那一轮收到的条目里连文件名都没有。
私信侧一直有这一步，群聊漏了。
"""
import json
import os
import tempfile
from datetime import datetime

from app.utils.multimodal import attachment_marks, text_attachment_blocks

TXT = {"name": "幻想小说创作自测试卷.txt", "path": "attachments/幻想小说创作自测试卷.txt",
       "size": 13943, "mime_type": "text/plain", "file_id": 167}
PNG = {"name": "shot.png", "path": "attachments/shot.png", "size": 2048, "mime_type": "image/png"}
CARD = {"type": "group_invitation", "invitation_id": 7, "group_name": "书友会"}


class _Msg:
    def __init__(self, content: str, attachments, *, revoked_at=None, mid=4181):
        self.id = mid
        self.content = content
        self.attachments = attachments
        self.revoked_at = revoked_at
        self.sender_type = "user"
        self.sender_id = 1
        self.sender_name = "ShuAICFR"
        self.created_at = datetime(2026, 11, 1, 14, 56)


def _write(data_dir: str, rel: str, body: str) -> None:
    path = os.path.join(data_dir, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)


def test_marks_name_the_file_and_its_size():
    assert attachment_marks([TXT]) == "[文件] 幻想小说创作自测试卷.txt（13.6 KB）"
    assert attachment_marks([TXT, PNG]) == "[文件] 幻想小说创作自测试卷.txt（13.6 KB） [图片]"
    assert attachment_marks([CARD]) == "", "邀请卡片不是文件"
    assert attachment_marks(None) == ""


def test_image_mark_is_not_repeated_when_the_text_already_says_it():
    """正文已经写了 [图片]（QQ 入站的占位），就不再补一个 → 同一条消息只该出现一次

    2026-11-14 真机：AI 收到「[图片] [图片]」两个标记，回"还是只有[图片]两个字"。
    """
    assert attachment_marks([PNG], said="[图片]") == ""
    assert attachment_marks([PNG], said="看这个 [图片] 好看吗") == ""
    assert attachment_marks([PNG], said="看这个") == "[图片]", "正文没提图时仍要补标记"
    assert attachment_marks([TXT, PNG], said="[图片]") == "[文件] 幻想小说创作自测试卷.txt（13.6 KB）"


def test_group_entry_carries_attachments():
    from app.chat.gm import gm_message_entry

    text = gm_message_entry(_Msg("我帮你发过来。", [TXT]), agent_name="鲸鱼娘", agent_user_id=120)["content"]

    assert "[文件] 幻想小说创作自测试卷.txt（13.6 KB）" in text
    assert "我帮你发过来。" in text


def test_dm_entry_carries_attachments_in_the_same_way():
    from app.chat.dm import dm_message_entry

    msg = _Msg("", [TXT])
    msg.attachments = json.dumps([TXT])      # 私信的 attachments 是 Text 列（JSON 字符串）

    text = dm_message_entry(msg, agent_name="鲸鱼娘", agent_user_id=120)["content"]

    assert "[文件] 幻想小说创作自测试卷.txt（13.6 KB）" in text


def test_revoked_message_drops_the_marks_too():
    from app.chat.gm import gm_message_entry

    text = gm_message_entry(_Msg("我帮你发过来。", [TXT], revoked_at=datetime(2026, 11, 1, 15, 0)),
                            agent_name="鲸鱼娘", agent_user_id=120)["content"]

    assert "[文件]" not in text


def test_text_attachment_content_is_inlined_and_clipped():
    body = "第一行\n" + "字" * 4000
    with tempfile.TemporaryDirectory() as data_dir:
        _write(data_dir, TXT["path"], body)

        block = text_attachment_blocks([TXT], data_dir)

    assert block.startswith("[文件内容] 幻想小说创作自测试卷.txt")
    assert f"全文 {len(body)} 字" in block, block[:80]
    assert "第一行" in block
    assert "后面还有" in block
    assert len(block) < 4000, "超长内容被裁剪，不会整份塞进上下文"


def test_binary_and_missing_attachments_are_skipped():
    with tempfile.TemporaryDirectory() as data_dir:
        block = text_attachment_blocks([PNG, dict(TXT, path="attachments/不存在.txt")], data_dir)

    assert block == ""


def test_oversized_text_attachment_is_not_inlined():
    with tempfile.TemporaryDirectory() as data_dir:
        _write(data_dir, TXT["path"], "x" * (70 * 1024))

        block = text_attachment_blocks([TXT], data_dir)

    assert "太大没有内联" in block
    assert "x" * 100 not in block


def test_latest_message_gets_the_file_content():
    from app.ai.llm import _attach_attachments_to_message

    with tempfile.TemporaryDirectory() as data_dir:
        _write(data_dir, TXT["path"], "题目一：写一个开头。")
        messages = [{"role": "system", "content": "在群聊「测试」中："},
                    {"role": "user", "content": "[文件] 幻想小说创作自测试卷.txt（13.6 KB） 我帮你发过来。"}]

        injected = _attach_attachments_to_message(messages, 1, _Msg("我帮你发过来。", [TXT]), data_dir)

    assert injected == 0, "没有图片"
    assert "[文件内容]" in messages[1]["content"]
    assert "题目一：写一个开头。" in messages[1]["content"]


async def test_picture_followed_by_a_text_message_still_reaches_the_ai():
    """先发图、再补一句"收到了吗"：图仍要进本轮上下文

    2026-11-14 真机：群里先发一张图、紧接着发文字，AI 被那条文字唤醒——它收到的"最新一条
    用户消息"没有附件，图就成了历史里的 [图片] 标记，于是回"我这边没收到内容"。所以要在
    本轮范围内往回找最近的一张图（仍只注入一张，不跨轮）。
    """
    from app.ai.llm import _turn_image_of

    class _Row:
        def __init__(self, attachments):
            self.attachments = attachments

    class _Db:
        def __init__(self, rows):
            self.rows = rows
            self.asked: list = []

        async def get(self, model, pk):
            self.asked.append(pk)
            return self.rows.get(pk)

    rows = {100: _Row([PNG]), 101: _Row(None)}
    slots = [(3, {"actor": "user", "ref": "100"}), (4, {"actor": "user", "ref": "101"})]

    db = _Db(rows)
    index, orm = await _turn_image_of(db, slots, object, 4, rows[101])
    assert (index, orm) == (3, rows[100]), (index, orm)

    # 最后一条自己带图：直接用，不必往回翻
    db2 = _Db(rows)
    index2, orm2 = await _turn_image_of(db2, slots, object, 4, rows[100])
    assert index2 == 4 and orm2 is rows[100]
    assert db2.asked == [], db2.asked
