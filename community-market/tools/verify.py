#!/usr/bin/env python3
"""社区插件索引校验器 — CI 与本地共用同一份规则。

为什么要有它：社区层"CI 通过即收录"，那 CI 就得真的验一遍；同时索引里的 README 目录
必须由 index.json 生成，否则手写列表一定会和索引漂移。

校验分两层：
1) 索引层（离线，永远跑）：字段、id slug、唯一性、类别、sha256 形态、verified 必须有审阅记录
2) 制品层（--online 或 --dist）：核对 sha256 → 解包校验 plugin.json 的 id/version 与索引一致
   → 禁用文件与体积上限

客户端还有一份硬限制（backend/app/services/plugin/store.py）：那是最后一道闸。CI 只负责
"提前告诉作者不合规"，两边故意不共享代码（社区仓要能独立跑）。

用法：
    python3 tools/verify.py --index index.json                 # 只校验索引
    python3 tools/verify.py --index index.json --online        # 下载发布物校验
    python3 tools/verify.py --index index.json --dist ./dist   # 用本地制品校验（离线复现）
    python3 tools/verify.py --index index.json --render        # 重新生成 README 目录
    python3 tools/verify.py --index index.json --check-render  # 目录过时即失败
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sys
import urllib.request
import zipfile
from pathlib import Path

MAX_PACKAGE_BYTES = 5 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED = 20 * 1024 * 1024
MAX_ENTRIES = 500
MAX_FILE_BYTES = 2 * 1024 * 1024

CATEGORIES = {"skin", "skill", "world", "service", "other"}
TIERS = {"verified", "community"}
BANNED_SUFFIXES = (
    ".exe", ".dll", ".so", ".dylib", ".bin", ".msi", ".bat", ".cmd",
    ".sh", ".ps1", ".jar", ".class", ".pyc", ".pyd",
)
BANNED_PARTS = (".git", "__pycache__", ".venv", "node_modules", ".idea")

SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,39}$")
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+")
SOURCE_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[A-Za-z0-9_.-]+$")
ASSET_RE = re.compile(r"^[A-Za-z0-9_.-]+\.zip$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
# 外部身份类别（通道插件在 plugin.json 的 channel.kind 里声明）：短名，全局唯一，先到先得
KIND_RE = re.compile(r"^[a-z][a-z0-9_-]{2,31}$")
RESERVED_KINDS = {"federation", "local", "system"}

README_BEGIN = "<!-- CATALOG:BEGIN -->"
README_END = "<!-- CATALOG:END -->"


def load_index(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def validate_index(data: dict) -> list:
    """索引层校验：只碰 JSON，不联网——作者提 PR 时就能拿到反馈"""
    errors = []
    if data.get("version") != 1:
        errors.append("index.version 必须是 1")
    packages = data.get("packages")
    if not isinstance(packages, list):
        return errors + ["index.packages 必须是数组"]

    seen = set()
    kinds: dict[str, str] = {}
    for i, pkg in enumerate(packages):
        where = "packages[" + str(i) + "]"
        if not isinstance(pkg, dict):
            errors.append(where + " 必须是对象")
            continue
        pid = str(pkg.get("id") or "")
        where = "packages[" + str(i) + "](" + (pid or "?") + ")"
        if not SLUG_RE.match(pid):
            errors.append(where + " id 不合法（字母数字开头，含 . _ -，≤40）")
        if pid in seen:
            errors.append(where + " id 重复")
        seen.add(pid)

        if pkg.get("category") not in CATEGORIES:
            errors.append(where + " category 必须是 " + "/".join(sorted(CATEGORIES)))
        if not VERSION_RE.match(str(pkg.get("version") or "")):
            errors.append(where + " version 必须是 x.y.z 形式")
        if not SOURCE_RE.match(str(pkg.get("source") or "")):
            errors.append(where + " source 必须是 owner/repo@ref")
        if not ASSET_RE.match(str(pkg.get("asset") or "")):
            errors.append(where + " asset 必须是 zip 文件名（不含路径）")
        if not SHA_RE.match(str(pkg.get("sha256") or "")):
            errors.append(where + " sha256 必须是 64 位小写十六进制")

        tier = pkg.get("tier")
        if tier not in TIERS:
            errors.append(where + " tier 必须是 verified/community")
        # verified 意味着"我们自己审过"，没有审阅记录就不许这么标
        if tier == "verified" and not (pkg.get("reviewed_by") and pkg.get("reviewed_at")):
            errors.append(where + " tier=verified 必须带 reviewed_by 与 reviewed_at")
        if pkg.get("yanked") and not pkg.get("yank_reason"):
            errors.append(where + " 已下架必须写 yank_reason")

        # 通道插件声明的外部身份类别：全局唯一（先到先得），保留字不给用
        kind = pkg.get("channel_kind")
        if kind is not None:
            kind = str(kind)
            if not KIND_RE.match(kind):
                errors.append(where + " channel_kind 必须是小写短名（a-z0-9_-，3~32 位，字母开头）")
            elif kind in RESERVED_KINDS:
                errors.append(where + " channel_kind「" + kind + "」是平台保留字")
            elif kind in kinds:
                errors.append(where + " channel_kind「" + kind + "」已被 " + kinds[kind] + " 占用")
            else:
                kinds[kind] = pid
    return errors


def _member_ok(name: str) -> bool:
    parts = [p for p in name.replace("\\", "/").split("/") if p]
    if not parts or any(p == ".." for p in parts):
        return False
    if any(p in BANNED_PARTS for p in parts) or any(p.startswith(".") for p in parts):
        return False
    return not name.lower().endswith(BANNED_SUFFIXES)


def check_artifact(pkg: dict, data: bytes) -> list:
    """制品层校验：这份 zip 到底是不是索引里写的那一份，且内容干净"""
    pid = pkg["id"]
    errors = []
    if len(data) > MAX_PACKAGE_BYTES:
        return [pid + ": 包体积超过 5MB"]
    digest = hashlib.sha256(data).hexdigest()
    if digest != pkg["sha256"]:
        return [pid + ": sha256 不匹配（索引 " + pkg["sha256"][:12] + "… 实际 " + digest[:12]
                + "…）——发布物改过就要更新索引"]

    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return [pid + ": 不是有效的 zip"]
    with zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if len(infos) > MAX_ENTRIES:
            errors.append(pid + ": 文件数超过 " + str(MAX_ENTRIES))
        if sum(i.file_size for i in infos) > MAX_TOTAL_UNCOMPRESSED:
            errors.append(pid + ": 解压后超过 20MB")
        for info in infos:
            name = info.filename.replace("\\", "/")
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                errors.append(pid + ": 含符号链接 " + name)
            elif not _member_ok(name):
                errors.append(pid + ": 含不允许的条目 " + name)
            elif info.file_size > MAX_FILE_BYTES:
                errors.append(pid + ": 单文件过大 " + name)

        manifests = [i for i in infos if i.filename.replace("\\", "/").split("/")[-1] == "plugin.json"]
        if not manifests:
            errors.append(pid + ": 包里没有 plugin.json")
        else:
            try:
                manifest = json.loads(zf.read(manifests[0]).decode("utf-8"))
            except Exception as e:
                errors.append(pid + ": plugin.json 解析失败 " + str(e))
                manifest = None
            if isinstance(manifest, dict):
                if manifest.get("id") != pid:
                    errors.append(pid + ": plugin.json 的 id 与索引不一致（" + str(manifest.get("id")) + "）")
                if not str(manifest.get("version") or "").startswith(pkg["version"]):
                    errors.append(pid + ": plugin.json 的 version 与索引不一致（" + str(manifest.get("version")) + "）")
                if manifest.get("category") and manifest["category"] != pkg["category"]:
                    errors.append(pid + ": plugin.json 的 category 与索引不一致")
                # 通道插件：包内声明的类别必须与索引一致；索引没写就等于放弃"类别唯一"的保护
                block = manifest.get("channel")
                declared = str((block or {}).get("kind") or "") if isinstance(block, dict) else ""
                if declared and not pkg.get("channel_kind"):
                    errors.append(pid + ": plugin.json 声明了 channel.kind，索引里必须写 channel_kind（类别唯一性靠索引守）")
                elif declared and declared != pkg.get("channel_kind"):
                    errors.append(pid + ": plugin.json 的 channel.kind 与索引 channel_kind 不一致（" + declared + "）")
                elif pkg.get("channel_kind") and not declared:
                    errors.append(pid + ": 索引写了 channel_kind，但 plugin.json 没有声明 channel.kind")
    return errors


def fetch_asset(pkg: dict, token) -> bytes:
    owner_repo, ref = pkg["source"].split("@", 1)
    url = "https://github.com/" + owner_repo + "/releases/download/" + ref + "/" + pkg["asset"]
    req = urllib.request.Request(url, headers={"User-Agent": "copree-plugin-index-ci"})
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def render_catalog(packages: list) -> str:
    lines = [
        "| 插件 | 类型 | 版本 | 信任层级 | 来源 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for pkg in sorted(packages, key=lambda p: str(p.get("id", ""))):
        tier_label = "已验证" if pkg.get("tier") == "verified" else "社区（CI 校验）"
        if pkg.get("yanked"):
            tier_label += " · 已下架"
        name = pkg.get("name") or pkg.get("id", "")
        desc = (pkg.get("description") or "").strip().replace("|", "/")
        if desc:
            name += " — " + desc
        lines.append("| " + name + " | " + str(pkg.get("category", "")) + " | " + str(pkg.get("version", ""))
                     + " | " + tier_label + " | " + pkg.get("source", "") + " |")
    if len(lines) == 2:
        lines.append("| _暂无收录_ | | | | |")
    return "\n".join(lines)


def update_readme(readme: Path, table: str, check_only: bool) -> list:
    text = readme.read_text(encoding="utf-8")
    begin = text.index(README_BEGIN) + len(README_BEGIN)
    end = text.index(README_END)
    new_text = text[:begin] + "\n\n" + table + "\n\n" + text[end:]
    if new_text == text:
        return []
    if check_only:
        return ["README 目录与 index.json 不一致，请跑 --render 重新生成"]
    readme.write_text(new_text, encoding="utf-8")
    return []


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", default="index.json")
    parser.add_argument("--readme", default="README.md")
    parser.add_argument("--online", action="store_true", help="下载 pinned 发布物核对 sha256")
    parser.add_argument("--dist", help="用本地目录里的 zip 核对 sha256（离线复现 CI）")
    parser.add_argument("--render", action="store_true", help="重新生成 README 目录")
    parser.add_argument("--check-render", action="store_true", help="目录过时即失败")
    args = parser.parse_args()

    data = load_index(Path(args.index))
    errors = validate_index(data)
    if errors:
        print("索引校验失败：")
        for e in errors:
            print("  - " + e)
        return 1
    packages = data["packages"]
    print("索引校验通过：" + str(len(packages)) + " 个条目")

    if args.online or args.dist:
        token = os.environ.get("GITHUB_TOKEN")
        for pkg in packages:
            try:
                blob = (Path(args.dist) / pkg["asset"]).read_bytes() if args.dist else fetch_asset(pkg, token)
            except Exception as e:
                errors.append(pkg["id"] + ": 取制品失败 " + str(e))
                continue
            errors.extend(check_artifact(pkg, blob))
        if errors:
            print("制品校验失败：")
            for e in errors:
                print("  - " + e)
            return 1
        print("制品校验通过：sha256 与 manifest 都对得上")

    readme = Path(args.readme)
    if readme.is_file():
        errors = update_readme(readme, render_catalog(packages), args.check_render and not args.render)
        if errors:
            print("目录校验失败：")
            for e in errors:
                print("  - " + e)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
