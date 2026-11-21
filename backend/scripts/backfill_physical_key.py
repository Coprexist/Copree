"""一次性补齐历史文件的物理键（file_metadata.physical_key）。

为什么需要：physical_key 是 0120 才加的列，而它是"这两行是不是同一份字节"的唯一凭据——
ai_fetch_attachment 用 os.link 收附件时会多建一行元数据、size 照抄原件，不按物理键去重就把
一份文件算成两份用量，清掉原件也释放不了磁盘（设计 docs/dev/message_storage_and_cleanup.md §5.7）。
Postgres 看不到 inode，所以只能由这个脚本对着磁盘 stat 一遍。

关键约束：**只有"当前仍持有字节"的那一行能拿键**。同一个路径被覆盖过就留下多行历史版本，
而 open(path,'wb') 是原地截断、inode 不变，这些历史行 stat 出来会指向同一份新字节——
2026-11-19 实测 attachments/image.png 有 27 行历史版本共用一个 inode。所以按路径只认
id 最大的那一行，其余留 NULL（它们的字节早没了，不该参与计量，也不该被当成"同组还有活行"）。
与 file_service._claim_bytes 的不变式一致。

幂等：只处理还没有键的行。文件已不在磁盘上的路径同样留 NULL——宁可少去重，也不编假键。
用法（迁移 0120 之后）：
    docker exec -w /app ai_group_backend python scripts/backfill_physical_key.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, text  # noqa: E402

from app.database import async_session  # noqa: E402
from app.models.file import FileMetadata  # noqa: E402
from app.services.content.file_service import physical_key_of  # noqa: E402


async def main() -> None:
    async with async_session() as db:
        rows = (await db.execute(
            select(FileMetadata)
            .where(FileMetadata.physical_key.is_(None))
            .order_by(FileMetadata.path, FileMetadata.id.desc())
        )).scalars().all()
        claimed = set()
        filled = skipped = missing = 0
        for row in rows:
            if row.path in claimed:
                skipped += 1          # 同一路径的历史版本：字节已经不在了
                continue
            key = physical_key_of(row.path)
            if not key:
                missing += 1
                continue
            row.physical_key = key
            claimed.add(row.path)
            filled += 1
        await db.commit()
        print(f"补了 {filled} 个路径的物理键；跳过 {skipped} 行历史版本；{missing} 个路径文件不在磁盘上")

        # 顺手报一下去重后的口径：行数 vs 物理份数 vs 两种算法各自的字节数
        total_rows, total_bytes, distinct_keys = (await db.execute(text(
            "SELECT count(*), sum(size), count(DISTINCT COALESCE(physical_key, 'f:' || id)) "
            "FROM file_metadata"
        ))).one()
        deduped_bytes = (await db.execute(text(
            "SELECT sum(size) FROM ("
            "  SELECT DISTINCT ON (COALESCE(physical_key, 'f:' || id)) size"
            "  FROM file_metadata"
            "  ORDER BY COALESCE(physical_key, 'f:' || id), id"
            ") t"
        ))).scalar()
        print(
            f"元数据 {total_rows} 行 → 物理 {distinct_keys} 份；"
            f"按行累加 {total_bytes or 0} 字节，按物理去重 {deduped_bytes or 0} 字节"
        )


if __name__ == "__main__":
    asyncio.run(main())
