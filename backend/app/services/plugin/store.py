"""
插件商城 · 一期（本地安装包）— 上传包 → 审阅 manifest → 安装 / 更新 / 卸载

为什么要独立一层：安装本质是「把一个 zip 变成磁盘上的插件目录」的纯文件操作。
把它和 DB 同步（catalog.sync_plugins_to_db）、运行时登记（apply_skill_plugins）分开，
安全审查就只有这一处（越界路径、符号链接、体积、条目数、id 合法性），也能脱离数据库单测。

包格式（尽量宽容，宽容的部分都在这里收敛）：
- zip；plugin.json 在包根目录，或整个插件被套在一个顶层目录里（自动下钻一层）
- 安装目标 = catalog 扫描的用户插件目录 DATA_DIR/plugins/<id>/，安装包仓库 = DATA_DIR/plugin_packages/

安全边界（与用户约定的一致）：
- 只写 DATA_DIR，绝不碰 backend/plugins（内置插件）；id 撞内置 → 拒绝，不允许用户目录覆盖内置
- 拒绝绝对路径、..、符号链接、隐藏目录（.git/__pycache__）、可执行后缀
- 限单包体积 / 解压后总体积 / 条目数 / 单文件体积
- 覆盖安装先落到临时目录再原子替换：失败不会留下半个插件
- plugin.py 会被导入执行，所以「先亮 manifest（作者/版本/配置项/来源/大小/sha256）再安装」是产品约束
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import shutil
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from app.services.plugin import catalog

logger = logging.getLogger(__name__)

MANIFEST_NAME = catalog.MANIFEST_NAME
PACKAGE_DIR = catalog.USER_PLUGIN_DIR.parent / "plugin_packages"

MAX_PACKAGE_BYTES = 5 * 1024 * 1024            # 单个安装包上限
MAX_TOTAL_UNCOMPRESSED = 20 * 1024 * 1024      # 解压后总体积上限（防 zip 炸弹）
MAX_ENTRIES = 500                              # 条目数上限
MAX_FILE_BYTES = 2 * 1024 * 1024               # 单文件上限

CATEGORIES = ("skin", "skill", "world", "service", "other")

# 这些后缀出现在插件包里没有正当理由：插件行为走 plugin.py，不靠外部可执行文件
BANNED_SUFFIXES = (
    ".exe", ".dll", ".so", ".dylib", ".bin", ".msi", ".bat", ".cmd",
    ".sh", ".ps1", ".jar", ".class", ".pyc", ".pyd",
)
BANNED_PARTS = (".git", "__pycache__", ".venv", "node_modules", ".idea")


class PackageError(ValueError):
    """安装包不合法（内容问题，直接回给管理员看）"""


def _slug_ok(plugin_id: str) -> bool:
    """插件 id 同时是目录名，必须能安全拼路径：字母数字开头，其余限 [a-z0-9_.-]"""
    if not plugin_id or len(plugin_id) > 40:
        return False
    if not (plugin_id[0].isalnum() and plugin_id[0].isascii()):
        return False
    return all(c.isascii() and (c.isalnum() or c in "_.-") for c in plugin_id)


def _member_ok(name: str) -> bool:
    """条目路径安全：拒绝绝对路径、..、隐藏目录、可执行后缀"""
    parts = PurePosixPath(name).parts
    if not parts:
        return False
    if name.startswith("/") or any(p == ".." for p in parts):
        return False
    if any(p in BANNED_PARTS for p in parts):
        return False
    if any(p.startswith(".") for p in parts):
        return False
    return not name.lower().endswith(BANNED_SUFFIXES)


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    """zip 里的符号链接靠 external_attr 高 16 位标记；我们不落盘链接，一律跳过"""
    return (info.external_attr >> 16) & 0o170000 == 0o120000


def _normalize_manifest(raw: Any, fallback_id: str) -> dict:
    if not isinstance(raw, dict):
        raise PackageError("plugin.json 必须是 JSON 对象")
    plugin_id = str(raw.get("id") or fallback_id).strip()
    if not _slug_ok(plugin_id):
        raise PackageError("插件 id 不合法：" + (plugin_id or "(空)") + "（只允许字母数字开头，含 . _ -，≤40 字符）")
    category = str(raw.get("category") or "other").strip()
    if category not in CATEGORIES:
        raise PackageError("未知插件类别：" + category + "（可选 " + "/".join(CATEGORIES) + "）")
    return {
        "id": plugin_id,
        "name": str(raw.get("name") or plugin_id)[:120],
        "description": str(raw.get("description") or "")[:2000],
        "category": category,
        "version": str(raw.get("version") or "1.0.0")[:20],
        "author": str(raw.get("author") or "")[:80],
        "entry": str(raw.get("entry") or catalog.ENTRY_FILE.get(category, ""))[:80],
        "default_enabled": bool(raw.get("default_enabled", True)),
    }


def _read_zip(zip_bytes: bytes) -> tuple[dict, str, list[tuple[str, zipfile.ZipInfo]]]:
    """校验并拆包：返回 (manifest, 包内前缀, 待落盘条目)。

    只读不写——上传时先审阅，确认后再安装，两步共用这一份校验。
    """
    if len(zip_bytes) > MAX_PACKAGE_BYTES:
        raise PackageError("安装包过大：" + str(len(zip_bytes) // 1024) + "KB（上限 " + str(MAX_PACKAGE_BYTES // 1024 // 1024) + "MB）")
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile:
        raise PackageError("无效的 zip 文件")

    with zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if len(infos) > MAX_ENTRIES:
            raise PackageError("包内文件过多：" + str(len(infos)) + "（上限 " + str(MAX_ENTRIES) + "）")
        total = sum(i.file_size for i in infos)
        if total > MAX_TOTAL_UNCOMPRESSED:
            raise PackageError("解压后体积过大：" + str(total // 1024 // 1024) + "MB")

        usable: list[tuple[str, zipfile.ZipInfo]] = []
        for info in infos:
            name = info.filename.replace("\\", "/")
            if _is_symlink(info):
                logger.warning("安装包内含符号链接，已跳过：%s", name)
                continue
            if not _member_ok(name):
                logger.warning("安装包内含不安全条目，已跳过：%s", name)
                continue
            if info.file_size > MAX_FILE_BYTES:
                raise PackageError("包内单文件过大：" + name)
            usable.append((name, info))

        manifests = [n for n, _ in usable if PurePosixPath(n).name == MANIFEST_NAME]
        if not manifests:
            raise PackageError("包内找不到 " + MANIFEST_NAME + "（插件根目录需要一份 manifest）")
        # plugin.json 允许在根或某个顶层目录里；多处出现说明包结构有歧义
        depths = {len(PurePosixPath(n).parts) for n in manifests}
        if len(depths) > 1:
            raise PackageError("包内有多个 " + MANIFEST_NAME + "，无法确定插件根目录")
        depth = depths.pop()
        if depth > 2:
            raise PackageError(MANIFEST_NAME + " 层级过深（应在包根或一层目录内）")
        manifest_name = manifests[0]
        prefix = str(PurePosixPath(manifest_name).parent)
        prefix = "" if prefix == "." else prefix

        try:
            raw = json.loads(zf.read(manifest_name).decode("utf-8"))
        except Exception as e:
            raise PackageError(MANIFEST_NAME + " 解析失败：" + str(e))
        fallback_id = PurePosixPath(manifest_name).parts[0] if depth == 2 else ""
        manifest = _normalize_manifest(raw, fallback_id)

        inner: list[tuple[str, zipfile.ZipInfo]] = []
        for name, info in usable:
            rel = name[len(prefix) + 1:] if prefix else name
            if rel:
                inner.append((rel, info))
        return manifest, prefix, inner


def inspect_package(zip_bytes: bytes) -> dict[str, Any]:
    """审阅安装包：只读校验 + 返回给界面看的 manifest 预览"""
    manifest, _prefix, inner = _read_zip(zip_bytes)
    return {
        "manifest": manifest,
        "file_count": len(inner),
        "uncompressed_bytes": sum(i.file_size for _, i in inner),
        "sha256": hashlib.sha256(zip_bytes).hexdigest(),
        "files": sorted(rel for rel, _ in inner)[:50],
    }


def _installed_version(plugin_id: str) -> str | None:
    """已安装版本：直接读磁盘 manifest（不经过 DB，安装/卸载后立刻就是准的）"""
    manifest_file = catalog.USER_PLUGIN_DIR / plugin_id / MANIFEST_NAME
    if not manifest_file.is_file():
        return None
    try:
        return str(json.loads(manifest_file.read_text(encoding="utf-8")).get("version") or "1.0.0")
    except Exception:
        return None


def is_builtin(plugin_id: str) -> bool:
    return (catalog.BUILTIN_PLUGIN_DIR / plugin_id / MANIFEST_NAME).is_file()


def _decorate(info: dict, file_name: str) -> dict:
    """补上界面要用的三个判断：装没装、能不能更新、会不会撞内置"""
    plugin_id = info["manifest"]["id"]
    installed = _installed_version(plugin_id)
    return {
        **info,
        "file_name": file_name,
        "installed": installed is not None,
        "installed_version": installed,
        "builtin_conflict": is_builtin(plugin_id),
        "updatable": installed is not None and installed != info["manifest"]["version"],
    }


def list_packages() -> list[dict[str, Any]]:
    """扫描安装包仓库；坏包不隐藏、也不让整个列表挂掉——标记 error 交给界面显示"""
    if not PACKAGE_DIR.is_dir():
        return []
    out = []
    for path in sorted(PACKAGE_DIR.glob("*.zip")):
        try:
            out.append(_decorate(inspect_package(path.read_bytes()), path.name))
        except PackageError as e:
            out.append({"file_name": path.name, "error": str(e), "size": path.stat().st_size})
        except Exception as e:  # 磁盘读失败等，同样只影响这一项
            out.append({"file_name": path.name, "error": "读取失败：" + str(e), "size": 0})
    return out


def save_package(file_name: str, data: bytes) -> dict[str, Any]:
    """存包（不安装）：先校验，再落盘。文件名只取 basename，防止借 .. 写到仓库外面。"""
    info = inspect_package(data)          # 校验失败直接抛 PackageError
    safe_name = PurePosixPath(file_name.replace("\\", "/")).name
    if not safe_name.lower().endswith(".zip"):
        safe_name = info["manifest"]["id"] + "-" + info["manifest"]["version"] + ".zip"
    PACKAGE_DIR.mkdir(parents=True, exist_ok=True)
    target = PACKAGE_DIR / safe_name
    tmp = target.with_suffix(target.suffix + ".part")
    tmp.write_bytes(data)
    tmp.replace(target)
    logger.info("插件安装包已入库：%s（%s KB，%s）", safe_name, len(data) // 1024, info["manifest"]["id"])
    return _decorate(info, safe_name)


def install_package(zip_bytes: bytes, *, upgrade: bool = False) -> dict[str, Any]:
    """安装 / 更新：解到临时目录 → 原子替换 → 清掉旧版本。

    覆盖安装走「临时目录 + 替换」，任何一步失败都保留原目录，不会留下半个插件。
    """
    info = inspect_package(zip_bytes)
    manifest, _prefix, inner = _read_zip(zip_bytes)
    plugin_id = manifest["id"]

    if is_builtin(plugin_id):
        raise PackageError("「" + plugin_id + "」是内置插件，不能被用户包覆盖（换个 id，或直接改内置目录）")

    target = catalog.USER_PLUGIN_DIR / plugin_id
    if target.exists() and not upgrade:
        raise PackageError("「" + manifest["name"] + "」已安装（" + str(_installed_version(plugin_id)) + "），请用更新")

    catalog.USER_PLUGIN_DIR.mkdir(parents=True, exist_ok=True)
    staging = catalog.USER_PLUGIN_DIR / (".staging-" + plugin_id + "-" + uuid.uuid4().hex[:8])
    staging.mkdir(parents=True)
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            for rel, member in inner:
                dest = staging / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(zf.read(member))
        if not (staging / MANIFEST_NAME).is_file():
            raise PackageError("安装后缺少 " + MANIFEST_NAME + "，包结构无法下钻")
        backup: Path | None = None
        if target.exists():
            backup = target.with_name(".trash-" + plugin_id + "-" + uuid.uuid4().hex[:8])
            target.replace(backup)
        staging.replace(target)
        if backup is not None:
            shutil.rmtree(backup, ignore_errors=True)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    logger.info("插件%s：%s v%s（%s 个文件）", "更新" if upgrade else "安装", plugin_id, manifest["version"], len(inner))
    return {**info, "file_name": "", "installed": True, "installed_version": manifest["version"], "updatable": False, "builtin_conflict": False}


def uninstall_plugin(plugin_id: str) -> None:
    """卸载用户插件：只删 DATA_DIR/plugins 下的目录，内置插件一律不动"""
    if not _slug_ok(plugin_id):
        raise PackageError("插件 id 不合法：" + plugin_id)
    if is_builtin(plugin_id):
        raise PackageError("「" + plugin_id + "」是内置插件，不能卸载")
    target = (catalog.USER_PLUGIN_DIR / plugin_id).resolve()
    root = catalog.USER_PLUGIN_DIR.resolve()
    if root not in target.parents:
        raise PackageError("拒绝删除插件目录之外的路径")
    if not target.is_dir():
        raise PackageError("插件未安装：" + plugin_id)
    shutil.rmtree(target)
    logger.info("插件已卸载：%s", plugin_id)


def delete_package(file_name: str) -> None:
    safe_name = PurePosixPath(file_name.replace("\\", "/")).name
    target = (PACKAGE_DIR / safe_name).resolve()
    root = PACKAGE_DIR.resolve()
    if root not in target.parents or not target.is_file():
        raise PackageError("安装包不存在：" + safe_name)
    target.unlink()
    logger.info("插件安装包已删除：%s", safe_name)
