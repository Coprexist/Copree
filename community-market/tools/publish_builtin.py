#!/usr/bin/env python3
"""把主仓的内置插件发布到社区索引：源码归档 + 制品 zip + index 条目。

为什么要脚本：条目里三个字段必须彼此一致（version / asset / sha256），
手抄一次就会漂；而它们全部由「插件目录 + git tag」推得出来。

产物：
  verified/<id>/            源码（我们仓里审过的，才配 tier: verified）
  dist/<id>-<version>.zip   制品（根目录含 plugin.json）
  index.json                条目 upsert（同 id 覆盖，不保留历史版本）

用法：
  python3 tools/publish_builtin.py --plugins ../../backend/plugins \
      --index index.json --dist dist --source Coprexist/Copree@v0.4.16
  python3 tools/verify.py --index index.json --readme README.md --render --dist dist
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import zipfile
from datetime import date, timezone, datetime
from pathlib import Path

SKIP_DIRS = {"__pycache__", ".git", ".pytest_cache", "node_modules"}
SKIP_SUFFIXES = {".pyc", ".pyo", ".DS_Store"}

# 这些是"随实例部署"的内置插件：它们本来就在主仓，发布到社区是为了
# 非官方部署（旧版本、自建客户端）也能装上，所以 tier 用 verified（源码在我们仓里）。
DEFAULT_IDS = [
    "bilibili",
    "qq-channel",
    "qq-napcat",
    "emoji-qq",
    "keyword-autoreply",
    "skin-aurora",
    "skill-writing-studio",
]


def _build_zip(src: Path, out: Path) -> bytes:
    """根目录含 plugin.json 的 zip；返回内容字节（同一份字节用来算 sha256）。"""
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(src.rglob("*")):
            if path.is_dir():
                continue
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            if path.suffix in SKIP_SUFFIXES:
                continue
            zf.write(path, path.relative_to(src).as_posix())
    return out.read_bytes()


def _manifest(plugin_dir: Path) -> dict:
    return json.loads((plugin_dir / "plugin.json").read_text(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plugins", required=True, help="主仓 backend/plugins 目录")
    ap.add_argument("--index", default="index.json")
    ap.add_argument("--dist", default="dist")
    ap.add_argument("--source", required=True, help="owner/repo@ref，ref 必须是 tag")
    ap.add_argument("--ids", nargs="*", default=DEFAULT_IDS)
    ap.add_argument("--reviewed-by", default="Copree")
    args = ap.parse_args()

    plugins_root = Path(args.plugins)
    index_path = Path(args.index)
    dist = Path(args.dist)

    data = json.loads(index_path.read_text(encoding="utf-8"))
    packages = {p["id"]: p for p in data.get("packages", [])}
    today = date.today().isoformat()

    for plugin_id in args.ids:
        src = plugins_root / plugin_id
        if not (src / "plugin.json").exists():
            print(f"跳过 {plugin_id}：没有 plugin.json", file=sys.stderr)
            continue
        manifest = _manifest(src)
        version = str(manifest.get("version") or "1.0.0")
        # 不再把源码复制进本仓：单一真源是主仓 backend/plugins/<id>/，
        # 索引只记"哪个仓的哪个目录"（source_path），复制品会随主仓改动而漂。
        asset_name = f"{plugin_id}-{version}.zip"
        raw = _build_zip(src, dist / asset_name)
        sha = hashlib.sha256(raw).hexdigest()

        channel = manifest.get("channel") or {}
        entry = {
            "id": plugin_id,
            "name": str(manifest.get("name") or plugin_id)[:120],
            "description": str(manifest.get("description") or "")[:2000],
            "category": str(manifest.get("category") or "other"),
            "author": str(manifest.get("author") or "")[:80],
            "version": version,
            "source": args.source,
            "asset": asset_name,
            "sha256": sha,
            "tier": "verified",
            "source_path": f"backend/plugins/{plugin_id}",
            "reviewed_by": args.reviewed_by,
            "reviewed_at": today,
        }
        if channel.get("kind"):
            entry["channel_kind"] = str(channel["kind"])
        packages[plugin_id] = entry
        print(f"已发布 {plugin_id} {version} → {asset_name} sha256={sha[:12]}…")

    data["packages"] = [packages[k] for k in sorted(packages)]
    data["version"] = 1
    data["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    index_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"索引已更新：{index_path}（{len(data['packages'])} 条）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
