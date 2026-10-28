"""附件 MIME 登记：AI 写的文件不再一律 text/plain（聊天把图片渲染成文件卡片的根因）。

2026-10-28 报障：AI 在群里发的 exp_plot.svg 显示成文件卡片，点开只能下载。链路是
  ai_write_file 把新文件写死 mime_type=text/plain
  -> send_file 原样抄进消息 attachments 快照
  -> 前端 isImageAttachment 判 false -> 渲染成文件卡片
用例钉三层：扩展名判定、写入登记、存量行兜底（send_file 抄快照时用的 effective_mime）。
"""
import pytest
from sqlalchemy import text

from app.utils.mime import effective_mime, guess_mime, is_generic

pytestmark = pytest.mark.anyio


def test_guess_mime_covers_images():
    assert guess_mime("exp_plot.svg") == "image/svg+xml"
    assert guess_mime("A.PNG") == "image/png", "扩展名大小写不敏感"
    assert guess_mime("agents/1/pixel.webp") == "image/webp"
    assert guess_mime("notes.txt") == "text/plain"
    assert guess_mime("noext") == "text/plain"


def test_generic_and_effective_mime():
    assert is_generic("text/plain")
    assert is_generic("application/octet-stream")
    assert is_generic(None)
    assert not is_generic("image/svg+xml")
    # 存量行的 text/plain 按扩展名兜底：这就是 send_file 抄进消息快照的值
    assert effective_mime("agents/1/exp_plot.svg", "text/plain") == "image/svg+xml"
    # 已识别的类型不被扩展名覆盖（登记的是 jpeg 就按 jpeg）
    assert effective_mime("a.png", "image/jpeg") == "image/jpeg"


async def test_ai_write_file_registers_image_mime(migrated_db):
    """新写的 svg 直接登记成 image/svg+xml；存量 text/plain 行在被覆盖时自愈。"""
    from app.database import async_session
    from app.services.content.file_service import ai_write_file

    agent_id = 900001
    async with async_session() as db:
        svg = "<svg xmlns='http://www.w3.org/2000/svg'><rect width='1' height='1'/></svg>"
        meta = await ai_write_file(db, agent_id, "plot.svg", svg)
        assert meta.mime_type == "image/svg+xml", "写死的 text/plain 会让前端渲染成文件卡片"

        # 模拟修复前留下的存量行：同路径被别人改回 text/plain
        await db.execute(
            text("UPDATE file_metadata SET mime_type = 'text/plain' WHERE id = :i"),
            {"i": meta.id},
        )
        await db.commit()

        again = await ai_write_file(db, agent_id, "plot.svg", svg + "<circle r='1'/>")
        assert again.mime_type == "image/svg+xml", "覆盖写入必须顺手纠正存量行"

        txt = await ai_write_file(db, agent_id, "note.txt", "hi")
        assert txt.mime_type == "text/plain", "纯文本文件保持 text/plain"
        await db.rollback()
