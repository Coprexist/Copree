"""
世界文件服务 — 世界文件读写（隔离目录）

文件存储：data/worlds/{world_id}/（代码）+ data/worlds/{world_id}/data/（数据）
路径安全：拒绝 ../ 越界，所有操作限定在世界目录内。
"""
import logging
import shutil
import json
from pathlib import Path

logger = logging.getLogger(__name__)

WORLDS_ROOT = Path("data/worlds")

# ── 扩展名策略（单一来源：所有写入路径都过 _check_ext）────────────────
# 允许：世界代码 / 网页资源 / 纯文本源码 / 数据 / 媒体 / 字体。
# 下载与上传共用这一份清单（别再各写各的，2026-09-15 收敛）。
ALLOWED_EXTENSIONS = {
    # 世界代码与网页
    ".html", ".htm", ".css", ".js", ".mjs", ".json", ".map", ".py",
    # 纯文本源码 / 文档 / 配置 / 数据（AI 从网上复制代码进来用）
    ".md", ".txt", ".rst", ".tex", ".csv", ".xml", ".yaml", ".yml", ".toml",
    ".ini", ".cfg", ".conf", ".env", ".log", ".sql",
    ".ts", ".tsx", ".jsx", ".vue", ".svelte", ".java", ".c", ".h", ".cpp",
    ".hpp", ".cs", ".go", ".rs", ".kt", ".swift",
    # 媒体 / 字体
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".bmp",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".mp3", ".wav", ".ogg", ".mp4", ".webm",
    # 文档 / 压缩包（zip 内容由 import_zip 逐个过 _check_ext）
    ".pdf", ".zip",
}

# 禁止：可执行文件 / 安装包 / 系统库 / 宿主脚本
# 三层防护：① 提示词明写不得下载 ② 创建时 _check_ext 直接拒 ③ sweep_banned_files 兜底强删
BANNED_EXTENSIONS = {
    # Windows 可执行 / 安装包
    ".exe", ".msi", ".msp", ".msu", ".com", ".scr", ".pif", ".cpl",
    ".dll", ".sys", ".drv", ".ocx", ".inf", ".lnk", ".reg", ".hta", ".msc", ".gadget",
    # 控制台 / 宿主脚本（世界代码只有 .js/.py，其余脚本一律不要）
    ".bat", ".cmd", ".sh", ".bash", ".zsh", ".ksh", ".csh", ".fish",
    ".vbs", ".vbe", ".jse", ".wsf", ".wsh", ".ps1", ".psm1", ".psd1",
    # 跨平台可执行 / 包管理 / 磁盘镜像
    ".jar", ".class", ".apk", ".app", ".deb", ".rpm", ".dmg", ".pkg", ".snap",
    ".iso", ".img", ".bin", ".so", ".o", ".a", ".dylib", ".elf",
}
MAX_FILE_SIZE = 32 * 1024 * 1024  # 单文件 32MB（网页资源/下载文件用）

# ── 产物路径 ─────────────────────────────────────
# 构建产物/缓存对世界 AI 没有意义，列进上下文只是烧 token（世界只会更大）：
# file_list 默认不列、file_grep 的目录递归默认不搜，需要时由调用方显式放开。
ARTIFACT_DIRS = {
    "__pycache__", "node_modules", ".git", "dist", "build",
    ".venv", "venv", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".cache",
}
ARTIFACT_SUFFIXES = {".pyc", ".pyo", ".map"}
ARTIFACT_FILES = {".DS_Store", "Thumbs.db"}

# 目录递归搜索的护栏：一次调用最多扫这么多文件，超过 2MB 的单个文件跳过（防一次搜索卡死）
GREP_SCAN_FILE_LIMIT = 500
GREP_SCAN_BYTES_LIMIT = 2 * 1024 * 1024


def _world_dir(world_id: int) -> Path:
    """世界代码目录（自动创建）"""
    d = WORLDS_ROOT / str(world_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_path(world_id: int, rel_path: str) -> Path:
    """解析相对路径，防越界（../ 与绝对路径一律拒绝）"""
    rel_path = (rel_path or "").strip().lstrip("/")
    if not rel_path:
        raise ValueError("路径不能为空")
    if ".." in rel_path.split("/"):
        raise ValueError("非法路径: 不允许 .. 越界")
    base = _world_dir(world_id).resolve()
    target = (base / rel_path).resolve()
    if not str(target).startswith(str(base)):
        raise ValueError("非法路径: 越出世界目录")
    return target


class BannedFileError(ValueError):
    """禁用后缀（可执行/安装包/宿主脚本）——创建即拒，历史遗留由 sweep_banned_files 强删。

    继承 ValueError：路由层/工具层原有的 except ValueError 全部照常生效（无需改调用方）。
    """


def _check_ext(path: Path) -> None:
    """扩展名守卫（唯一入口）：禁用清单优先给明确告警，其次才查允许清单。"""
    ext = path.suffix.lower()
    if ext in BANNED_EXTENSIONS:
        raise BannedFileError(
            f"⛔ 禁止 {ext} 类型文件（可执行/安装包/脚本）：世界只允许网页资源与世界代码。"
            f"需要运行逻辑请写成 .py（沙箱执行）或前端 .js。"
        )
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(f"不允许的文件类型: {ext}，可选: {sorted(ALLOWED_EXTENSIONS)[:8]}...")


def is_artifact_path(rel_path: str) -> bool:
    """产物路径判定（file_list 与 file_grep 共用一条规则）：目录名命中，或文件名/后缀命中。"""
    parts = rel_path.replace("\\", "/").strip("/").split("/")
    if any(part in ARTIFACT_DIRS for part in parts[:-1]):
        return True
    name = parts[-1]
    return name in ARTIFACT_FILES or Path(name).suffix.lower() in ARTIFACT_SUFFIXES


def sweep_banned_files(world_id: int) -> list[str]:
    """全目录扫描，发现禁用后缀文件立即强制删除（返回被删的相对路径）。

    创建路径已逐个拦截，这里是兜底：手动拷进目录、历史遗留、解压夹带、改后缀绕过。
    调用点：世界唤醒、zip 导入、后端启动。
    """
    base = _world_dir(world_id)
    removed: list[str] = []
    for p in sorted(base.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in BANNED_EXTENSIONS:
            continue
        rel = str(p.relative_to(base))
        try:
            p.unlink()
            removed.append(rel)
        except OSError as e:                       # 权限/占用：留痕不阻断
            logger.warning(f"🛡️ 世界 #{world_id} 禁用文件删除失败: {rel}（{e}）")
    if removed:
        logger.warning(f"🛡️ 世界 #{world_id} 强制删除禁用后缀文件 {len(removed)} 个: {removed}")
    return removed


# ═══════════════════════════════════════════════════════════════
# 文件操作
# ═══════════════════════════════════════════════════════════════

def list_files(world_id: int, prefix: str = "") -> list[dict]:
    """文件树"""
    base = _world_dir(world_id)
    result = []
    for p in sorted(base.rglob("*")):
        if p.is_file():
            rel = str(p.relative_to(base))
            if prefix and not rel.startswith(prefix):
                continue
            result.append({
                "path": rel,
                "size": p.stat().st_size,
                "mtime": p.stat().st_mtime,
            })
    return result


def read_file(world_id: int, rel_path: str, offset: int | None = None, limit: int | None = None) -> dict:
    """读文件（文本按 utf-8，二进制返回大小）。

    offset/limit：按行分页读（1-based 行号）——大文件不用全读，
    先 file_grep 定位行号再读对应段落（对齐 OpenClaw read 工具）。
    """
    target = _safe_path(world_id, rel_path)
    if not target.is_file():
        raise FileNotFoundError(f"文件不存在: {rel_path}")
    data = target.read_bytes()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return {"path": rel_path, "binary": True, "size": len(data), "content": None}
    if offset is not None or limit is not None:
        lines = text.split("\n")
        total = len(lines)
        start = max(1, offset or 1)
        end = total if limit is None else min(total, start + limit - 1)
        start = min(start, total + 1)  # offset 超界 → 空段
        slice_lines = lines[start - 1:end]
        content = "\n".join(slice_lines)
        return {
            "path": rel_path, "content": content, "binary": False, "size": len(data),
            "total_lines": total, "start_line": start, "end_line": end,
            "truncated": end < total,
        }
    return {"path": rel_path, "content": text, "binary": False, "size": len(data), "total_lines": len(text.split("\n"))}


def _attach_context(lines: list[str], hits: list[dict], context: int) -> None:
    """给命中原地补 ±context 行上下文（带行号）。

    重叠区间合并去重：同一行只出现一次——命中行本身已在 content 里、其余命中行也不再进上下文，
    所以整份结果不会重复吐同一行（省 token）；区间超出文件首尾自动截断。
    """
    matched = {h["line"] for h in hits}
    emitted: set[int] = set()
    for hit in hits:
        start = max(1, hit["line"] - context)
        end = min(len(lines), hit["line"] + context)
        fresh = [n for n in range(start, end + 1) if n not in matched and n not in emitted]
        hit["context"] = [{"line": n, "content": lines[n - 1][:300]} for n in fresh]
        emitted.update(fresh)


def grep_file(world_id: int, rel_path: str, pattern: str, max_hits: int = 30, context: int = 0) -> dict:
    """按关键词/正则搜文件内容，返回命中行 + 行号（轻量定位，2026-08-13 新增）。

    对齐 OpenClaw 的 grep 用法：先定位再按需读，不用整文件全读。
    context>0 时每个命中附 ±context 行（带行号，命中行不重复）；context=0 的返回形状与旧版完全一致。
    """
    import re as _re
    target = _safe_path(world_id, rel_path)
    if not target.is_file():
        raise FileNotFoundError(f"文件不存在: {rel_path}")
    try:
        text = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return {"path": rel_path, "binary": True, "hits": [], "total_hits": 0}
    try:
        rx = _re.compile(pattern)
    except _re.error:
        # 非正则 → 当普通子串（大小写不敏感）
        rx = _re.compile(_re.escape(pattern), _re.IGNORECASE)
    lines = text.split("\n")
    hits = []
    for i, line in enumerate(lines, start=1):
        if rx.search(line):
            hits.append({"line": i, "content": line[:300]})
            if len(hits) >= max_hits:
                break
    if context > 0:
        _attach_context(lines, hits, context)
    return {"path": rel_path, "hits": hits, "total_hits": len(hits), "max_hits": max_hits}


def path_kind(world_id: int, rel_path: str) -> str:
    """路径类型：file / dir / missing（越界等非法路径照旧抛 ValueError）。"""
    target = _safe_path(world_id, rel_path)
    if target.is_file():
        return "file"
    if target.is_dir():
        return "dir"
    return "missing"


def _dir_scan_files(world_id: int, rel_path: str) -> tuple[list[str], int]:
    """目录递归展开为待搜文件（跳过产物路径与超大文件），返回（文件列表，跳过的超大文件数）。"""
    base = _world_dir(world_id).resolve()
    files: list[str] = []
    skipped_large = 0
    for p in sorted(_safe_path(world_id, rel_path).rglob("*")):
        if not p.is_file():
            continue
        rel = str(p.relative_to(base))
        if is_artifact_path(rel):
            continue
        if p.stat().st_size > GREP_SCAN_BYTES_LIMIT:
            skipped_large += 1
            continue
        files.append(rel)
    return files, skipped_large


def grep_paths(world_id: int, paths: list[str], pattern: str, max_hits: int = 30, context: int = 0) -> dict:
    """在文件/目录/多路径中搜索（file_grep 的目录与数组支持，2026-09-18 新增）。

    目录递归展开（跳过产物路径与超大文件），每条命中都带 path；
    命中累计到 max_hits、扫描累计到 GREP_SCAN_FILE_LIMIT 即停——多次调用与目录混传都不重复计。
    context>0 时每个命中附 ±context 行上下文（各文件独立去重）；context=0 时返回形状与旧版完全一致。
    single_file=True 表示只搜了一个文件，调用方据此保留原有的「不带 path」返回形状。
    """
    hits: list[dict] = []
    scanned = 0
    skipped_large = 0
    scan_truncated = False
    single_file = len(paths) == 1
    binary = False
    for rel_path in paths:
        kind = path_kind(world_id, rel_path)
        if kind == "missing":
            raise FileNotFoundError(f"文件不存在: {rel_path}")
        if kind == "file":
            files, skipped = [rel_path], 0
        else:
            single_file = False
            files, skipped = _dir_scan_files(world_id, rel_path)
        skipped_large += skipped
        for rel in files:
            if scanned >= GREP_SCAN_FILE_LIMIT:
                scan_truncated = True
                break
            scanned += 1
            one = grep_file(world_id, rel, pattern, max_hits=max_hits - len(hits), context=context)
            if one.get("binary"):
                binary = binary or single_file        # 单文件：沿用旧的 binary 返回
                continue
            for hit in one.get("hits") or []:
                hits.append({"path": rel, **hit})
            if len(hits) >= max_hits:
                break
        if len(hits) >= max_hits or scan_truncated:
            break
    return {
        "hits": hits,
        "files_scanned": scanned,
        "skipped_large": skipped_large,
        "single_file": single_file,
        "binary": binary,
        "truncated": len(hits) >= max_hits,
        "scan_truncated": scan_truncated,
    }


def write_file(world_id: int, rel_path: str, content: str) -> dict:
    """写文件（自动建目录）"""
    if len(content.encode("utf-8")) > MAX_FILE_SIZE:
        raise ValueError(f"文件超过 {MAX_FILE_SIZE // 1024 // 1024}MB 限制")
    target = _safe_path(world_id, rel_path)
    _check_ext(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    logger.info(f"🌐 世界 #{world_id} 写入文件: {rel_path} ({len(content)}B)")
    return {"path": rel_path, "size": len(content.encode("utf-8"))}


def write_file_bytes(world_id: int, rel_path: str, data: bytes) -> dict:
    """写二进制文件（图片/音频等上传用；校验与 write_file 同一套：白名单/越界/大小）"""
    if len(data) > MAX_FILE_SIZE:
        raise ValueError(f"文件超过 {MAX_FILE_SIZE // 1024 // 1024}MB 限制")
    target = _safe_path(world_id, rel_path)
    _check_ext(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    logger.info(f"🌐 世界 #{world_id} 写入二进制文件: {rel_path} ({len(data)}B)")
    return {"path": rel_path, "size": len(data)}


def delete_file(world_id: int, rel_path: str) -> None:
    """删除文件"""
    target = _safe_path(world_id, rel_path)
    if target.is_file():
        target.unlink()
        logger.info(f"🗑️ 世界 #{world_id} 删除文件: {rel_path}")
    elif target.is_dir():
        shutil.rmtree(target)
        logger.info(f"🗑️ 世界 #{world_id} 删除目录: {rel_path}")
    else:
        raise FileNotFoundError(f"不存在: {rel_path}")


def _tree_size(path: Path) -> int:
    """文件/目录字节数（复制前的大小校验；只累加文件本体）"""
    if path.is_file():
        return path.stat().st_size
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def _resolve_move(world_id: int, src: str, dst: str) -> tuple[Path, Path]:
    """移动/复制的公共前半段：两边都过越界检查，目标再过滤用后缀。"""
    source = _safe_path(world_id, src)
    if not source.exists():
        raise FileNotFoundError(f"不存在: {src}")
    target = _safe_path(world_id, dst)
    if source == target:
        raise ValueError("源与目标相同，无需操作")
    if source.is_file():
        _check_ext(target)                          # 目标文件名必须合法（目录名不查后缀）
    if target.is_dir():
        raise ValueError(f"目标已被目录占用: {dst}（目标要写完整文件名，不是目录）")
    return source, target


def move_file(world_id: int, src: str, dst: str) -> dict:
    """移动 / 重命名（跨目录搬移或同目录改名）；目录可整体搬移。"""
    source, target = _resolve_move(world_id, src, dst)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(target))
    removed = sweep_banned_files(world_id) if target.is_dir() else []
    logger.info(f"📦 世界 #{world_id} 移动文件: {src} → {dst}")
    return {"path": dst, "from": src, "banned_removed": removed}


def copy_file(world_id: int, src: str, dst: str) -> dict:
    """复制文件 / 目录（世界内复制；跨世界复制不支持——各自目录隔离）。"""
    source, target = _resolve_move(world_id, src, dst)
    size = _tree_size(source)
    if size > MAX_FILE_SIZE:
        raise ValueError(f"超过 {MAX_FILE_SIZE // 1024 // 1024}MB 限制（{size // 1024}KB）")
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, target)
        removed = sweep_banned_files(world_id)
    else:
        shutil.copy2(source, target)
        removed = []
    logger.info(f"📄 世界 #{world_id} 复制文件: {src} → {dst}")
    return {"path": dst, "from": src, "size": size, "banned_removed": removed}


# ═══════════════════════════════════════════════════════════════
# 文件夹导入（zip 或批量文件）
# ═══════════════════════════════════════════════════════════════

def import_zip(world_id: int, zip_bytes: bytes, exclude_content: bool = True) -> dict:
    """解压 zip 到世界目录（过滤越界与危险文件）。
    exclude_content=True（默认）：跳过 content/ 数据文件——数据是运行产物，导入包不该覆盖；
    商城导入（新世界）不受影响（新世界无 content）。"""
    import io
    import zipfile

    base = _world_dir(world_id)
    count = 0
    skipped_content = 0
    banned = 0
    meta: dict | None = None
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            for info in zf.infolist():
                # 安全：拒绝绝对路径与 ..
                name = info.filename.replace("\\", "/")
                if name.startswith("/") or ".." in name.split("/"):
                    continue
                if info.is_dir():
                    continue
                # 虚拟元数据条目：读回不落盘（随包配置载体）
                if name == "world_meta.json":
                    try:
                        meta = json.loads(zf.read(info).decode("utf-8"))
                    except Exception:
                        meta = None
                    continue
                if exclude_content and name.startswith("content/"):
                    skipped_content += 1
                    continue
                target = (base / name).resolve()
                if not str(target).startswith(str(base.resolve())):
                    continue
                try:
                    _check_ext(target)
                except BannedFileError:
                    banned += 1                      # 包里夹带可执行/脚本：跳过 + 计数告警
                    continue
                except ValueError:
                    continue
                if info.file_size > MAX_FILE_SIZE:
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(zf.read(info))
                count += 1
    except zipfile.BadZipFile:
        raise ValueError("无效的 zip 文件")
    removed = sweep_banned_files(world_id)           # 兜底：目录里已有的遗留禁用文件一并清掉
    logger.info(f"🌐 世界 #{world_id} zip 导入 {count} 个文件（跳过数据文件 {skipped_content}，禁用 {banned}，meta={'y' if meta else 'n'}）")
    return {
        "imported": count, "skipped_content": skipped_content,
        "banned_skipped": banned, "banned_removed": removed, "meta": meta,
    }


def export_zip(world_id: int, include_content: bool = True, meta: dict | None = None) -> bytes:
    """打包世界文件为 zip。代码/数据分离：
    include_content=True（默认）包含 content/ 产物区（静态文字数据，世界自己的产物）；
    False 只打包代码区（世界根目录 + skills 等），供世界发布用。
    meta 非空时以虚拟条目 world_meta.json 写入 zip（不落盘，世界目录零污染），
    用于携带运行配置（如 group_trigger_mode）随包分发——导入端读回合并。"""
    import io
    import json
    import zipfile

    base = _world_dir(world_id)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in base.rglob("*"):
            if p.is_file():
                rel = str(p.relative_to(base))
                if not include_content and rel.startswith("content/"):
                    continue
                zf.write(p, rel)
        if meta:
            zf.writestr("world_meta.json", json.dumps(meta, ensure_ascii=False, indent=2))
    logger.info(f"🌐 世界 #{world_id} 打包导出 {len(buf.getvalue())}B (include_content={include_content}, meta={'y' if meta else 'n'})")
    return buf.getvalue()
