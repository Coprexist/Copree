"""把人/AI 的附件搬进所有者自己的目录（步 5.1）

背景：附件以前全平铺在 data/attachments/ 下。两个用户传了同名文件（image.png、
语音.mp3 这种名字太常见），后一个 open(...,"wb") 就把前一个原地截断——字节换了主人，
两行元数据指向同一份内容。而且「这笔字节算谁的」只能靠状态位猜。路径即归属之后
（docs/dev/message_storage_and_cleanup.md §3.1），谁的空间就是谁的目录：

  attachments/<rest>    human:{uid} → users/{uid}/chat/<rest>
  attachments/voice/<n> ai:{id}     → agents/{id}/voice/<n>
  attachments/<rest>    ai:{id}     → agents/{id}/chat/<rest>
  agents/**、emoji/** 不动（已经在自己的目录里）

搬家用 os.replace：同一卷内是原子改名，不复制字节。同一 physical_key 的多行各改各的
路径，inode 不变，硬链接组（AI 把附件 fetch 进 inbox 的那份）原样保持。

同时重写消息快照里的 path：快照存的是「发出去那一刻的路径」，改名后旧消息的多模态
注入会读不到字节。三张表都存了快照（messages / dm_messages / world_chat_messages）。

幂等：路径已不在 attachments/ 下的行不再匹配，重复执行是空转。

用法：
  python scripts/migrate_owner_paths.py          # 只报告（dry-run）
  python scripts/migrate_owner_paths.py --apply  # 真搬
"""
import argparse
import asyncio
import json
import logging
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asyncpg  # noqa: E402

from scripts._shared import parse_db_url  # noqa: E402
from app.utils.pure.paths import unique_sibling  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("owner_paths")

ATTACHMENT_ROOT = "attachments"

# 存了附件快照的表：表名 → 列类型（jsonb 要走 ::jsonb，text 直接写字符串）
SNAPSHOT_TABLES = (
    ("messages", "jsonb"),
    ("dm_messages", "text"),
    ("world_chat_messages", "jsonb"),
)


def target_of(owner_type: str, owner_id: int, path: str) -> str | None:
    """站内路径 → 按所有者归属的新路径（纯函数：单测与迁移共用同一口径）

    只处理 attachments/ 下的行。human 进自己的对话目录，ai 进自己的沙箱；system /
    group 的（表情包、群文件）不归个人空间，返回 None 表示「不动」。
    """
    prefix = ATTACHMENT_ROOT + "/"
    if not path.startswith(prefix):
        return None
    rest = path[len(prefix):]
    if not rest:
        return None
    oid = int(owner_id)
    if owner_type == "human":
        return f"users/{oid}/chat/{rest}"
    if owner_type == "ai":
        voice = "voice/"
        if rest.startswith(voice):
            return f"agents/{oid}/voice/{rest[len(voice):]}"
        return f"agents/{oid}/chat/{rest}"
    return None


def move_bytes(root: Path, old: str, new: str, file_id: int) -> str:
    """把 old 的字节改名到 new，返回实际落点

    只有记录没有字节（覆盖写留下的历史版本）时什么都不做，直接返回目标路径——
    这类行的 physical_key 本来就是空的，搬不搬都不影响用量。
    """
    src = root / old
    dst = root / new
    if not src.exists():
        return new
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        if (dst.stat().st_dev, dst.stat().st_ino) == (src.stat().st_dev, src.stat().st_ino):
            return new      # 同一个 inode：重复执行，收工
        new = unique_sibling(new, file_id)
        dst = root / new
    try:
        os.replace(src, dst)
    except OSError:
        shutil.move(str(src), str(dst))   # 跨卷兜底：如实复制+删除，不静默失败
    return new


async def _rewrite_snapshots(conn, mapping: dict[int, tuple[str, str]], apply: bool) -> int:
    """把消息快照里的 path 从旧路径改成新路径；返回改了多少条消息"""
    changed = 0
    for table, kind in SNAPSHOT_TABLES:
        rows = await conn.fetch(
            f"SELECT id, attachments::text AS body FROM {table} WHERE attachments IS NOT NULL"
        )
        touched_rows = 0
        for row in rows:
            try:
                atts = json.loads(row["body"])
            except (TypeError, ValueError):
                continue
            if not isinstance(atts, list):
                continue
            touched = False
            for att in atts:
                if not isinstance(att, dict):
                    continue
                fid = att.get("file_id")
                if not fid or int(fid) not in mapping:
                    continue
                old, new = mapping[int(fid)]
                if att.get("path") == old:
                    att["path"] = new
                    touched = True
            if not touched:
                continue
            touched_rows += 1
            if apply:
                payload = json.dumps(atts, ensure_ascii=False)
                if kind == "jsonb":
                    await conn.execute(
                        f"UPDATE {table} SET attachments=$1::jsonb WHERE id=$2", payload, row["id"]
                    )
                else:
                    await conn.execute(
                        f"UPDATE {table} SET attachments=$1 WHERE id=$2", payload, row["id"]
                    )
        if touched_rows:
            logger.info(f"  📝 {table}: {touched_rows} 条消息快照要改")
        changed += touched_rows
    return changed


def _prune_empty_dirs(root: Path) -> int:
    """搬完之后清掉空的 attachments/ 子目录（自底向上）"""
    base = root / ATTACHMENT_ROOT
    if not base.is_dir():
        return 0
    removed = 0
    for dirpath, _dirnames, _filenames in os.walk(base, topdown=False):
        p = Path(dirpath)
        if p == base:
            continue
        try:
            p.rmdir()
            removed += 1
        except OSError:
            pass
    return removed


async def migrate(conn, root: Path, apply: bool) -> int:
    # 带上 physical_key：只有它非空的那一行才真的持有字节。同一个 path 底下常有
    # 好几行历史版本（physical_key 为空），先处理它们会把字节搬进别人的目录，
    # 真正持有字节的那行反而指向一个空路径（2026-11-21 实测 id 264/279 就是一对）。
    rows = await conn.fetch(
        "SELECT id, owner_type, owner_id, path, physical_key FROM file_metadata "
        "WHERE path LIKE $1 ORDER BY id", ATTACHMENT_ROOT + "/%",
    )
    logger.info(f"  attachments/ 下共 {len(rows)} 行")

    mapping: dict[int, tuple[str, str]] = {}
    for row in rows:
        new = target_of(row["owner_type"], row["owner_id"], row["path"])
        if new is None:
            logger.warning(f"  ⚠️ 记录 {row['id']} 不归个人空间（{row['owner_type']}），跳过")
            continue
        if new == row["path"]:
            continue
        if apply:
            if row["physical_key"] is not None:
                new = move_bytes(root, row["path"], new, int(row["id"]))
            await conn.execute("UPDATE file_metadata SET path=$1 WHERE id=$2", new, row["id"])
        mapping[int(row["id"])] = (row["path"], new)
        logger.info(f"  {'✅' if apply else '·'} {row['id']}: {row['path']} → {new}")

    snapshots = await _rewrite_snapshots(conn, mapping, apply)
    if apply:
        pruned = _prune_empty_dirs(root)
        logger.info(f"  清理空目录 {pruned} 个")
    logger.info(
        f"  记录 {len(mapping)} 条、快照 {snapshots} 条"
        + ("" if apply else "（dry-run，加 --apply 才真改）")
    )
    return len(mapping)


async def main() -> None:
    parser = argparse.ArgumentParser(description="把附件搬进所有者自己的目录")
    parser.add_argument("--apply", action="store_true", help="真搬（默认只报告）")
    args = parser.parse_args()

    url = os.getenv("DATABASE_URL") or os.getenv("DATABASE_URL_SYNC", "")
    if not url:
        logger.error("未设置 DATABASE_URL")
        sys.exit(1)

    from app.config import settings

    root = Path(settings.data_dir)
    logger.info(f"数据根：{root}" + ("" if args.apply else "（dry-run）"))
    conn = await asyncpg.connect(**parse_db_url(url))
    try:
        await migrate(conn, root, args.apply)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
