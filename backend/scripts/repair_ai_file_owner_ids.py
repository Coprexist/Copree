"""把 AI 文件记录里的虚拟账号 id 归正成 agent.id

背景：2026-07 那批 AI 文件由早期代码写入，owner_id 存成了 agents.user_id（AI 自己的
虚拟账号，例如 16、41），path 也跟着写成 agents/16/。agent 10 的沙箱是 agents/10，
这些文件 AI 自己反而读不到；权限检查还得为它们留一段按 user_id 兜底的查询。

规则：
  - 只处理 owner_type='ai' 且 owner_id 不是现存 agent.id 的记录。
  - 按 agents.user_id 找到唯一归属（user_id 是唯一键），把 owner_id 改成 agent.id、
    path 前缀 agents/{old}/ → agents/{agent.id}/，并搬物理文件。
  - 目标位置已有同名文件时以沙箱那份为准，旧副本归档到 .superseded/
    （与 migrate_ai_files_to_sandbox.py 同策略）；源文件不在则只改记录。
  - 幂等：归正过的记录 owner_id 已是 agent.id，不再匹配。
  - 找不到唯一归属的行只报告，不动。

用法：
  python scripts/repair_ai_file_owner_ids.py            # 只报告（dry-run）
  python scripts/repair_ai_file_owner_ids.py --apply    # 真改
"""
import argparse
import asyncio
import logging
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asyncpg  # noqa: E402

from scripts._shared import parse_db_url  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("ai_file_owner_repair")

PREFIX = "agents"


def load_data_dir() -> Path:
    from app.config import settings
    return Path(settings.data_dir)


async def repair(conn, root: Path, apply: bool) -> int:
    rows = await conn.fetch(
        "SELECT f.id, f.owner_id, f.path FROM file_metadata f "
        "WHERE f.owner_type = 'ai' "
        "AND NOT EXISTS (SELECT 1 FROM agents a WHERE a.id = f.owner_id) "
        "ORDER BY f.owner_id, f.id"
    )
    if not rows:
        logger.info("  ℹ️ 没有需要归正的 AI 文件记录")
        return 0

    done = 0
    for row in rows:
        owner = await conn.fetch(
            "SELECT id FROM agents WHERE user_id = $1", row["owner_id"]
        )
        if len(owner) != 1:
            logger.warning(
                f"  ⚠️ 记录 {row['id']} 的 owner_id={row['owner_id']} 找不到唯一 AI"
                f"（命中 {len(owner)} 个），跳过"
            )
            continue

        agent_id = owner[0]["id"]
        old_prefix = f"{PREFIX}/{row['owner_id']}/"
        rel = row["path"]
        new_rel = (
            f"{PREFIX}/{agent_id}/{rel[len(old_prefix):]}"
            if rel.startswith(old_prefix)
            else f"{PREFIX}/{agent_id}/{rel}"
        )
        old_phys, new_phys = root / rel, root / new_rel

        if not old_phys.exists():
            note = "源文件不在，只改记录"
        elif new_phys.exists():
            note = "目标已存在（以沙箱那份为准），旧副本归档到 .superseded/"
            if apply:
                archive = new_phys.parent / ".superseded" / old_phys.name
                archive.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(old_phys), str(archive))
        else:
            note = "搬入正确文件空间"
            if apply:
                new_phys.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(old_phys), str(new_phys))

        if apply:
            await conn.execute(
                "UPDATE file_metadata SET owner_id = $1, path = $2, updated_at = NOW() WHERE id = $3",
                agent_id, new_rel, row["id"],
            )
        done += 1
        logger.info(
            f"  {'✅' if apply else '·'} 记录 {row['id']}: owner {row['owner_id']} → {agent_id}，"
            f"{rel} → {new_rel}（{note}）"
        )
    return done


async def main() -> None:
    parser = argparse.ArgumentParser(description="AI 文件 owner_id 归正（虚拟账号 id → agent.id）")
    parser.add_argument("--apply", action="store_true", help="真改（默认只报告）")
    args = parser.parse_args()

    url = os.getenv("DATABASE_URL") or os.getenv("DATABASE_URL_SYNC", "")
    if not url:
        logger.error("未设置 DATABASE_URL")
        sys.exit(1)

    root = load_data_dir()
    logger.info(f"数据根：{root}" + ("" if args.apply else "（dry-run，加 --apply 才真改）"))
    conn = await asyncpg.connect(**parse_db_url(url))
    try:
        count = await repair(conn, root, args.apply)
        logger.info(f"🎉 处理 {count} 条记录" + ("，已写入" if args.apply else "（未改动，加 --apply 执行）"))
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
