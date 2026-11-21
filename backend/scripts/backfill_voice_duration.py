"""一次性补齐历史语音的时长（file_metadata.duration_ms + 消息快照里的 duration_ms）。

为什么需要：duration_ms 是 0116 才加的列。此前的语音（AI 的 send_voice、人上传的音频）
元数据与消息快照里都没有它，会话列表只能写回「[语音]」。这个脚本按磁盘上的文件重新探测：
1) 先补 file_metadata（音频行的源头）；
2) 再把 messages / dm_messages 里纯音频附件快照的 duration_ms 补上——快照是"引用这一刻"
   的样子，历史消息要跟着变，只能改快照本身（列表不再查库，与 0116 同一个理由）。

幂等：只处理缺时长的行，跑几遍都不会动别的行。
用法（迁移 0116 之后）：
    docker exec -w /app ai_group_backend python scripts/backfill_voice_duration.py
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select  # noqa: E402

from app.database import async_session  # noqa: E402
from app.models.dm import DMMessage  # noqa: E402
from app.models.file import FileMetadata  # noqa: E402
from app.models.message import Message  # noqa: E402
from app.services.content.file_service import disk_path  # noqa: E402
from app.utils.audio_probe import audio_duration_ms  # noqa: E402
from app.utils.message_serializer import normalize_attachments  # noqa: E402


def _load(path: str) -> bytes | None:
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


async def main() -> None:
    async with async_session() as db:
        # 1) file_metadata：缺时长的音频行
        rows = (await db.execute(
            select(FileMetadata).where(
                FileMetadata.mime_type.like("audio/%"),
                FileMetadata.duration_ms.is_(None),
            )
        )).scalars().all()
        filled = 0
        for row in rows:
            data = _load(disk_path(row.path))
            ms = audio_duration_ms(data, row.mime_type) if data else None
            if ms:
                row.duration_ms = ms
                filled += 1
        await db.commit()
        print(f"file_metadata 补了 {filled} 行")

        # 2) 消息快照：纯音频附件缺 duration_ms 的（用刚补完的元数据）
        known = {
            int(fid): int(ms) for fid, ms in (await db.execute(
                select(FileMetadata.id, FileMetadata.duration_ms).where(
                    FileMetadata.mime_type.like("audio/%"),
                )
            )).all() if ms
        }
        patched = 0
        for model in (Message, DMMessage):
            for msg in (await db.execute(select(model))).scalars().all():
                atts = normalize_attachments(getattr(msg, "attachments", None))
                if not atts or not isinstance(atts, list):
                    continue
                changed = False
                for att in atts:
                    if not isinstance(att, dict):
                        continue
                    if not str(att.get("mime_type") or "").startswith("audio/"):
                        continue
                    if att.get("duration_ms"):
                        continue
                    ms = known.get(int(att.get("file_id") or 0))
                    if ms:
                        att["duration_ms"] = ms
                        changed = True
                if changed:
                    # Text 列存的是 JSON 字符串，JSONB 是对象：两种都按"重新赋值"写回，
                    # 原地改字典不会触发 ORM 的变更追踪
                    msg.attachments = (
                        json.dumps(atts, ensure_ascii=False)
                        if isinstance(msg.attachments, str) else atts
                    )
                    patched += 1
        await db.commit()
        print(f"消息快照补了 {patched} 条")


if __name__ == "__main__":
    asyncio.run(main())
