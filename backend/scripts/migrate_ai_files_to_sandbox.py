"""把 AI 的存量文件从 data/ 根搬进它自己的文件空间 data/agents/{id}/

背景：AI 的 file_* 工具以前直接落在 data/ 根，而 run_script、记忆、OpenCLI 的工作目录是
data/agents/{id}/（app/paths.agent_dir）——同一个「我的文件」分成两处，两侧还各留一份同名
不同内容的文件。现在 file_* 也经 file_service.ai_stored_path 落到 agents/{id}/，存量记录要跟着走。

规则：
  - 只处理 file_metadata 里 owner_type='ai' 且 path 不以 agents/ 开头的记录。
  - 目标位置已有同名文件时**不覆盖**（沙箱里那份才是真在跑的），只把记录指过去，旧文件原地留着。
  - 幂等：搬过的记录不再匹配；中断重跑不会重复搬。

用法：
  python scripts/migrate_ai_files_to_sandbox.py            # 只报告（dry-run）
  python scripts/migrate_ai_files_to_sandbox.py --apply    # 真搬
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
logger = logging.getLogger("ai_file_migrate")

PREFIX = "agents"


def load_data_dir() -> Path:
    from app.config import settings
    return Path(settings.data_dir)


async def migrate(conn, root: Path, apply: bool) -> int:
    rows = await conn.fetch(
        "SELECT id, owner_id, path FROM file_metadata "
        "WHERE owner_type = 'ai' AND path NOT LIKE $1 ORDER BY owner_id, path",
        PREFIX + "/%",
    )
    if not rows:
        logger.info("  ℹ️ 没有需要迁移的 AI 文件记录")
        return 0

    for row in rows:
        old_rel = row["path"]
        new_rel = f"{PREFIX}/{row['owner_id']}/{old_rel}"
        old_phys, new_phys = root / old_rel, root / new_rel

        if not old_phys.exists():
            note = "源文件不在，只改记录"
        elif new_phys.exists():
            # 沙箱那一份才是真在跑的：不覆盖，但把被取代的旧副本挪出 data/ 根
            # （留着迟早有人拿它当"改成功了"，file_* 与脚本本来就不该再有两份）
            note = "目标已存在（以沙箱那份为准），旧副本归档到 .superseded/"
            if apply:
                archive = new_phys.parent / ".superseded" / old_phys.name
                archive.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(old_phys), str(archive))
        else:
            note = "搬入文件空间"
            if apply:
                new_phys.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(old_phys), str(new_phys))
        if apply:
            await conn.execute("UPDATE file_metadata SET path = $1 WHERE id = $2", new_rel, row["id"])
        logger.info(f"  {'✅' if apply else '·'} [AI #{row['owner_id']}] {old_rel} → {new_rel}（{note}）")
    return len(rows)


async def main() -> None:
    parser = argparse.ArgumentParser(description="AI 文件空间迁移（data/ 根 → data/agents/{id}/）")
    parser.add_argument("--apply", action="store_true", help="真搬（默认只报告）")
    args = parser.parse_args()

    url = os.getenv("DATABASE_URL") or os.getenv("DATABASE_URL_SYNC", "")
    if not url:
        logger.error("未设置 DATABASE_URL")
        sys.exit(1)

    root = load_data_dir()
    logger.info(f"数据根：{root}" + ("" if args.apply else "（dry-run，加 --apply 才真搬）"))
    conn = await asyncpg.connect(**parse_db_url(url))
    try:
        count = await migrate(conn, root, args.apply)
        logger.info(f"🎉 处理 {count} 条记录" + ("，已写入" if args.apply else "（未改动，加 --apply 执行）"))
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
