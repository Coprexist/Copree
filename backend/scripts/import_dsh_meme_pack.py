"""把 dsh-meme 的图库导入成 Copree 的 emojipack 插件（纯资源包）

dsh-meme 的库是纯文件夹：manifest.json + index.db(sqlite) + memes/<tag>/<file>。
caption / keywords 是模型学图时写进 index.db 的——那是"AI 能读懂"的那一层，原样搬过来，
不重新生成、也不改语义。

写到哪里：默认 <data>/plugins/<pack-id>/（用户安装插件目录，git 不跟踪）。
重跑即更新：图片按来源覆盖，描述以 index.db 为准，插件目录里多出来的旧图会被清掉。

用法（在 backend 目录下）：
    .venv/bin/python scripts/import_dsh_meme_pack.py \
        --library /path/to/dsh-meme/memes/dafeiyu-001 --pack-id emoji-dafeiyu
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
# 数据目录：仓库布局是 <repo>/data；容器里 backend 挂在 /app、数据就是 /app/data
DATA_DIR = (BACKEND.parent / "data") if BACKEND.name == "backend" else (BACKEND / "data")
PLUGINS_DIR = DATA_DIR / "plugins"

SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp")
ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")


def _face_id(tag: str, stem: str) -> str:
    """短码：<tag>_<文件名>，只留小写字母数字/下划线/横线，且必须以字母开头"""
    raw = f"{tag}_{stem}".lower()
    raw = re.sub(r"[^a-z0-9_-]+", "_", raw).strip("_")
    if not raw or not raw[0].isalpha():
        raw = f"m_{raw}"
    return raw[:40]


def _load_index(db_path: Path) -> list[dict]:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT path, tag, file_name, caption, COALESCE(keywords, '') AS keywords FROM memes"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def build_pack(library: Path, out_dir: Path, pack_id: str) -> dict:
    manifest = json.loads((library / "manifest.json").read_text(encoding="utf-8"))
    rows = _load_index(library / "index.db")
    faces = []
    copied = set()
    for row in rows:
        rel = str(row.get("path") or "").strip()
        src = library / rel
        if not rel or not src.is_file() or src.suffix.lower() not in SUFFIXES:
            print(f"跳过（图不在或不是图片）：{rel}")
            continue
        tag = str(row.get("tag") or "other").strip() or "other"
        stem = Path(str(row.get("file_name") or src.name)).stem
        dest_rel = f"{tag}/{src.name}"
        dest = out_dir / dest_rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        copied.add(dest_rel)
        caption = str(row.get("caption") or "").strip()
        faces.append({
            "id": _face_id(tag, stem),
            "name": (caption[:24] or stem),
            "file": dest_rel,
            "caption": caption,
            "keywords": str(row.get("keywords") or "").strip(),
            "mood": tag,
        })

    # 清掉上一次导入留下、这次不再出现的图（库删了图，这里也不该留着）
    for path in out_dir.rglob("*"):
        if path.is_file() and path.suffix.lower() in SUFFIXES:
            rel = path.relative_to(out_dir).as_posix()
            if rel not in copied:
                path.unlink()

    plugin = {
        "id": pack_id,
        "name": str(manifest.get("name") or pack_id),
        "description": str(manifest.get("description") or "")[:200],
        "category": "emojipack",
        "version": str(manifest.get("version") or "1.0.0"),
        "author": "dsh-meme",
        "icon": "Smile",
        "default_enabled": True,
    }
    emoji = {
        "usage": (
            f"{len(faces)} 张表情：写 :短码: 或 [表情:名字] 即可发出，"
            "平台会把它当图片消息发送；标签/关键词供你挑选。"
        ),
        "faces": faces,
    }
    (out_dir / "plugin.json").write_text(
        json.dumps(plugin, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out_dir / "emoji.json").write_text(
        json.dumps(emoji, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"pack": pack_id, "faces": len(faces), "dir": str(out_dir)}


def main() -> int:
    ap = argparse.ArgumentParser(description="把 dsh-meme 图库导入成 Copree 表情包插件")
    ap.add_argument("--library", required=True, help="dsh-meme 的库目录（含 manifest.json 与 index.db）")
    ap.add_argument("--pack-id", default="emoji-dafeiyu", help="插件 id（目录名），默认 emoji-dafeiyu")
    ap.add_argument("--out", default="", help="输出目录（默认 <data>/plugins/<pack-id>）")
    args = ap.parse_args()

    library = Path(args.library).expanduser().resolve()
    if not (library / "manifest.json").is_file() or not (library / "index.db").is_file():
        print(f"不是 dsh-meme 的库目录：{library}")
        return 1
    if not ID_RE.match(args.pack_id):
        print(f"插件 id 不合法（小写字母开头、a-z0-9_-）：{args.pack_id}")
        return 1
    out_dir = Path(args.out).expanduser().resolve() if args.out else (PLUGINS_DIR / args.pack_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    result = build_pack(library, out_dir, args.pack_id)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
