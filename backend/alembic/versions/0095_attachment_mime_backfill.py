"""附件 MIME 回填：AI 写的 svg/png 以前一律登记成 text/plain

2026-10-28 报障：群里 AI 画的 exp_plot.svg 在聊天里显示成文件卡片，点开只能下载。
根因是 ai_write_file 把新文件的 mime_type 写死 text/plain（已在源码修掉），但存量
有两处仍是错的：
  1) file_metadata.mime_type —— 文件列表与下载响应头跟着它；
  2) messages / dm_messages / world_chat_messages 的 attachments JSON 快照 ——
     前端渲染只看这份快照，不回表查 files。
所以这里一次性回填。规则与 app/utils/mime.py 的显式表一致：只认图片后缀，
文本/html 之类不动（它们的 text/plain 本来就是对的）。

Revision ID: 0095
Revises: 0094
"""
import json
import os

import sqlalchemy as sa
from alembic import op

revision = "0095"
down_revision = "0094"
branch_labels = None
depends_on = None

IMAGE_MIMES = {
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".ico": "image/x-icon",
    ".bmp": "image/bmp",
    ".avif": "image/avif",
}
GENERIC = ("", "text/plain", "application/octet-stream", "binary/octet-stream")
ATTACHMENT_TABLES = ("messages", "dm_messages", "world_chat_messages")


def _image_mime(name) -> str | None:
    ext = os.path.splitext(str(name or ""))[1].lower()
    return IMAGE_MIMES.get(ext)


def _rewrite(atts):
    """把附件数组里「类型没识别 + 后缀是图片」的项改对；返回 (新数组, 改动数)。"""
    changed = 0
    out = []
    for att in atts or []:
        if isinstance(att, dict):
            mime = str(att.get("mime_type") or "").strip().lower()
            if mime in GENERIC:
                target = _image_mime(att.get("name") or att.get("path"))
                if target:
                    att = {**att, "mime_type": target}
                    changed += 1
        out.append(att)
    return out, changed


def upgrade() -> None:
    bind = op.get_bind()
    # 数据回填用 PG 的 jsonb 操作；其它方言（本地 sqlite）保持原样，不影响功能
    if bind.dialect.name != "postgresql":
        return

    fixed_files = 0
    rows = bind.execute(sa.text(
        "SELECT id, path, mime_type FROM file_metadata "
        "WHERE mime_type IS NULL OR lower(mime_type) IN "
        "('text/plain','application/octet-stream','binary/octet-stream')"
    )).fetchall()
    for fid, path, _mime in rows:
        target = _image_mime(path)
        if not target:
            continue
        bind.execute(
            sa.text("UPDATE file_metadata SET mime_type = :m WHERE id = :i"),
            {"m": target, "i": fid},
        )
        fixed_files += 1

    fixed_atts = 0
    for table in ATTACHMENT_TABLES:
        rows = bind.execute(sa.text(
            "SELECT id, attachments::text FROM " + table + " WHERE attachments IS NOT NULL"
        )).fetchall()
        for rid, raw in rows:
            try:
                atts = json.loads(raw)
            except (TypeError, ValueError):
                continue
            new_atts, changed = _rewrite(atts)
            if not changed:
                continue
            bind.execute(
                sa.text("UPDATE " + table + " SET attachments = CAST(:a AS jsonb) WHERE id = :i"),
                {"a": json.dumps(new_atts, ensure_ascii=False), "i": rid},
            )
            fixed_atts += changed

    print("[0095] 修正文件登记 " + str(fixed_files) + " 行、消息附件 " + str(fixed_atts) + " 项")


def downgrade() -> None:
    """改回 text/plain —— 旧值本来就错，这里只保证迁移可逆、不丢数据。"""
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    rows = bind.execute(sa.text(
        "SELECT id, path, mime_type FROM file_metadata WHERE mime_type LIKE 'image/%'"
    )).fetchall()
    for fid, path, mime in rows:
        if IMAGE_MIMES.get(os.path.splitext(str(path))[1].lower()) != mime:
            continue
        bind.execute(
            sa.text("UPDATE file_metadata SET mime_type = 'text/plain' WHERE id = :i"),
            {"i": fid},
        )
