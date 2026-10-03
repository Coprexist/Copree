"""
文件系统记忆索引服务

扫描 data_dir/agents/{agent_id}/memories/ 目录，生成 index.json 结构，
并格式化为系统提示词注入文本。与现有向量记忆（store_memory/recall_memory）共存。

目录结构：
    private/  — 个人信息、项目记录、自我反思（所有 AI 类型）
    shared/   — 全局经验、教学风格（半通用和共振 AI）
    cross/    — symlink → 全局共享（共振 AI 专属）

索引结构：
    {
      "directories": {
        "private": {
          "summary": "...",
          "files": {"文件名.md": {"summary": "...", "size": 1234}}
        },
        "shared": {...},
        "cross": {...}
      },
      "total_files": N,
      "total_size": B
    }
"""
import json
import logging
import os as _os
from app.paths import AGENTS_DIR, SHARED_MEMORIES_DIR

logger = logging.getLogger(__name__)

# 每类目录的默认 README 模板
DIRECTORY_README_TEMPLATES = {
    "private": (
        "# 私人记忆\n\n"
        "此目录存放你的私人信息——只有你自己能读写。\n\n"
        "建议子目录：\n"
        "- `个人信息/` — 关于用户的偏好、习惯、关系\n"
        "- `项目记录/` — 参与过的项目、讨论过的话题\n"
        "- `自我反思/` — 对自己的思考、成长记录\n"
    ),
    "shared": (
        "# 共享记忆\n\n"
        "此目录存放可与其他 AI 共享的经验和知识。\n\n"
        "建议子目录：\n"
        "- `全局经验/` — 可复用的对话策略、问题处理模式\n"
        "- `教学风格/` — 与用户互动中形成的教学/引导方法\n"
    ),
    "cross": (
        "# 跨实例记忆\n\n"
        "此目录通过符号链接指向全局共享记忆空间，\n"
        "所有共振 AI 共享此目录内容。\n"
    ),
}


def _get_memory_dir(agent_id: int) -> str:
    """获取指定 Agent 的记忆根目录路径"""
    return _os.path.join(str(AGENTS_DIR), str(agent_id), "memories")


async def init_memory_directories(agent_id: int, ai_type: str = "resonance") -> None:
    """
    为新创建的 Agent 初始化记忆目录骨架。

    ai_type 决定创建哪些目录：
    - general:      只有 private/
    - semi_general:  private/ + shared/
    - resonance:     private/ + shared/ + cross/（symlink）

    不阻塞主流程，失败只记日志。
    """
    try:
        base = _get_memory_dir(agent_id)

        # private/ — 所有 AI 都有
        _ensure_dir(_os.path.join(base, "private"))
        _write_if_not_exists(
            _os.path.join(base, "private", "README.md"),
            DIRECTORY_README_TEMPLATES["private"],
        )

        # shared/ — 半通用和共振 AI
        if ai_type in ("semi_general", "resonance"):
            _ensure_dir(_os.path.join(base, "shared"))
            _write_if_not_exists(
                _os.path.join(base, "shared", "README.md"),
                DIRECTORY_README_TEMPLATES["shared"],
            )

        # cross/ — 仅共振 AI，指向全局共享目录
        if ai_type == "resonance":
            cross_path = _os.path.join(base, "cross")
            global_shared = str(SHARED_MEMORIES_DIR)
            _ensure_dir(global_shared)
            if not _os.path.exists(cross_path):
                try:
                    _os.symlink(global_shared, cross_path, target_is_directory=True)
                except OSError:
                    # Windows 可能不支持 symlink，fallback 为普通目录 + README
                    _ensure_dir(cross_path)
                    _write_if_not_exists(
                        _os.path.join(cross_path, "README.md"),
                        DIRECTORY_README_TEMPLATES["cross"],
                    )

        logger.info(f"Agent {agent_id} 记忆目录初始化完成 (ai_type={ai_type})")
    except Exception as e:
        logger.warning(f"Agent {agent_id} 记忆目录初始化失败（非致命）: {e}")


async def generate_memory_index(agent_id: int) -> dict:
    """
    扫描记忆目录，生成索引 dict。

    返回格式（即使目录为空也返回合法结构）：
    {
      "directories": {
        "private": {"summary": "...", "files": {...}},
        ...
      },
      "total_files": 2,
      "total_size": 1801
    }
    """
    base = _get_memory_dir(agent_id)
    result: dict = {"directories": {}, "total_files": 0, "total_size": 0}

    if not _os.path.isdir(base):
        return result

    # 遍历顶层子目录（private, shared, cross）
    try:
        for entry in sorted(_os.scandir(base)):
            if not entry.is_dir():
                continue
            dir_name = entry.name
            dir_info = await _scan_directory(entry.path)
            if dir_info:
                result["directories"][dir_name] = dir_info
                result["total_files"] += dir_info.get("file_count", 0)
                result["total_size"] += dir_info.get("total_size", 0)
    except OSError as e:
        logger.warning(f"扫描记忆目录失败 (agent={agent_id}): {e}")

    return result


async def _scan_directory(dir_path: str) -> dict | None:
    """
    递归扫描一个目录，返回摘要信息。

    跳过 README.md（目录自带说明）和隐藏文件。
    对 .md 文件尝试提取第一行作为 summary。
    """
    files: dict[str, dict] = {}
    total_size = 0
    file_count = 0

    try:
        for entry in sorted(_os.scandir(dir_path), key=lambda e: e.name):
            # 跳过隐藏文件和 README
            if entry.name.startswith("."):
                continue
            if entry.name == "README.md":
                continue

            if entry.is_file():
                try:
                    stat = entry.stat()
                    size = stat.st_size
                    summary = ""
                    if entry.name.endswith(".md"):
                        summary = _extract_first_line(entry.path)
                    files[entry.name] = {
                        "summary": summary,
                        "size": size,
                    }
                    total_size += size
                    file_count += 1
                except OSError:
                    pass
            elif entry.is_dir() and not entry.is_symlink():
                # 递归子目录，作为嵌套展示
                sub_info = await _scan_directory(entry.path)
                if sub_info and sub_info.get("files"):
                    # 子目录作为一个虚拟"文件"条目，带 📁 前缀
                    sub_summary = f"{sub_info['file_count']} 个文件"
                    files[f"📁 {entry.name}/"] = {
                        "summary": sub_summary,
                        "size": sub_info["total_size"],
                        "_children": sub_info["files"],
                    }
                    total_size += sub_info["total_size"]
                    file_count += sub_info["file_count"]
    except OSError:
        pass

    if not files and file_count == 0:
        return None

    return {
        "summary": f"{file_count} 个文件，{_format_size(total_size)}",
        "files": files,
        "file_count": file_count,
        "total_size": total_size,
    }


def format_index_for_prompt(index: dict) -> str:
    """
    将记忆索引 dict 格式化为系统提示词注入文本。

    输出示例：
    ## 你的文件记忆库

    以下是你的长期记忆目录。你可以用 file_read 读取任何文件查看详细内容。

    📁 private/（3 个文件，12KB）
      📄 用户偏好.md（用户喜欢简洁回复）
      📄 与奶龙的关系.md（记录了与奶龙的互动）
      📁 项目记录/（2 个文件）

    📁 shared/（空）

    ---
    需要查看某个文件 → file_read("memories/private/用户偏好.md")
    需要写入新记忆 → file_write("memories/private/新发现.md", "内容")
    """
    if not index or index.get("total_files", 0) == 0:
        return ""

    lines: list[str] = [
        "## 你的文件记忆库",
        "",
        "以下是你的长期记忆目录。你可以用 file_read 读取任何文件查看详细内容。",
        "",
    ]

    for dir_name, dir_info in index.get("directories", {}).items():
        is_empty = dir_info.get("file_count", 0) == 0
        summary = dir_info.get("summary", "空")
        lines.append(f"📁 **{dir_name}/**（{summary}）")

        if is_empty:
            lines.append("  （空）")
        else:
            for file_name, file_info in dir_info.get("files", {}).items():
                file_summary = file_info.get("summary", "")
                size_str = _format_size(file_info.get("size", 0))
                if file_summary:
                    lines.append(f"  📄 {file_name}（{size_str}）— {file_summary}")
                else:
                    lines.append(f"  📄 {file_name}（{size_str}）")
        lines.append("")

    lines.extend([
        "---",
        "需要查看某个文件 → file_read(\"memories/private/文件名.md\")",
        "需要写入新记忆 → file_write(\"memories/private/新文件名.md\", \"内容\")",
        "需要列出文件 → file_list(\"memories/\")",
    ])

    return "\n".join(lines)


# ── 内部辅助 ──

def _ensure_dir(path: str) -> None:
    """确保目录存在"""
    _os.makedirs(path, exist_ok=True)


def _write_if_not_exists(path: str, content: str) -> None:
    """文件不存在时写入（不覆盖已有文件）"""
    if not _os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)


def _extract_first_line(path: str) -> str:
    """提取 .md 文件的第一行非空内容作为摘要（最多 80 字符）"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if stripped:
                    # 去掉 markdown 标题标记
                    stripped = stripped.lstrip("#").strip()
                    if len(stripped) > 80:
                        stripped = stripped[:77] + "..."
                    return stripped
    except Exception:
        pass
    return ""


def _format_size(size: int) -> str:
    """格式化文件大小"""
    if size < 1024:
        return f"{size}B"
    elif size < 1024 * 1024:
        return f"{size / 1024:.1f}KB"
    else:
        return f"{size / (1024 * 1024):.1f}MB"
