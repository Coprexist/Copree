"""file_list — 列文件

列出世界文件夹里的文件（网页代码等）。默认排除产物目录/文件，文件多时按顶层目录汇总，
用 prefix 只看某个子目录——世界越大越省上下文。
"""
from app.tools.world.base import WorldToolPlugin, WorldToolContext
from app.services.world.world_file_service import is_artifact_path, list_files

# 一次最多列这么多路径（再多模型要的其实是目录结构，用 prefix 再看具体的）
MAX_FILES = 50
# 汇总时最多列几个顶层目录
MAX_DIR_GROUPS = 12


def _dir_breakdown(paths: list[str]) -> dict:
    """按所在目录统计：文件多时给一张地图，目录名可直接当下一次 prefix 用，比几十条路径省 token

    只数子目录：根目录文件（index.html / main.py）单独用 root_files 列名字——
    汇总里给个 "(根目录)": 3 等于把最该看的三行藏起来（世界 AI 2026-10 反馈：只能靠 grep 反推）。
    """
    counts: dict[str, int] = {}
    for p in paths:
        if "/" not in p:
            continue
        parent = p.rsplit("/", 1)[0] + "/"
        counts[parent] = counts.get(parent, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1])[:MAX_DIR_GROUPS])


def _note(prefix: str, total: int, truncated: bool, excluded: int) -> str:
    """提示一行：为什么没列全、怎么缩小范围（省得模型反复试探）"""
    notes = []
    if not total:
        notes.append(f"没有匹配「{prefix}」的文件" if prefix else "世界文件夹是空的")
    if truncated:
        notes.append(f"共 {total} 个文件，超过 {MAX_FILES} 个只给目录汇总；用 prefix 缩小范围（如 prefix=\"js/game/\"）可看具体文件")
    if excluded:
        notes.append(f"已排除 {excluded} 个产物文件（__pycache__/node_modules/.git/dist 等），需要时传 include_artifacts=true")
    return "；".join(notes)


class FileListTool(WorldToolPlugin):
    name = 'file_list'
    label = '列文件'
    segment = 'file'

    description = (
        '列出世界文件夹里的文件（网页代码等）。默认不列 __pycache__/node_modules/.git/dist/*.pyc 等产物；'
        f'文件不超过 {MAX_FILES} 个时返回完整路径列表，超过时只给各目录的文件数汇总'
        '（用 prefix 钻进某个目录，如 prefix="js/game/"），比一次列全部省上下文。'
    )

    parameters = {'prefix': {'type': 'string', 'description': '可选：只看这个前缀/子目录（如 js/ 或 css/）'},
                  'include_artifacts': {'type': 'boolean',
                                        'description': '可选：true 时连产物目录/文件（__pycache__/dist 等）一起列'}}

    required = []

    async def execute(self, ctx: WorldToolContext) -> dict:
        try:
            args = ctx.args
            prefix = str(args.get("prefix") or "").strip().lstrip("/")
            paths = [f["path"] for f in list_files(ctx.world.id, prefix)]
            excluded = 0
            if not args.get("include_artifacts"):
                kept = [p for p in paths if not is_artifact_path(p)]
                excluded = len(paths) - len(kept)
                paths = kept
            over = len(paths) > MAX_FILES
            result = {
                "success": True,
                "total": len(paths),
                "shown": 0 if over else len(paths),
                "truncated": over,
                "files": [] if over else paths,
            }
            if prefix:
                result["prefix"] = prefix
            if excluded:
                result["excluded"] = excluded
            if result["truncated"]:
                result["dirs"] = _dir_breakdown(paths)
                root_files = [p for p in paths if "/" not in p]
                if root_files:
                    result["root_files"] = root_files
            note = _note(prefix, result["total"], result["truncated"], excluded)
            if note:
                result["note"] = note
            return result
        except Exception as e:
            return {"success": False, "error": str(e)}

    def summary(self, result: dict) -> str:
        if not result.get("success"):
            return f"列文件失败：{result.get('error', '未知错误')}"
        files = result.get("files") or []
        total = result.get("total", len(files))
        if not total:
            return result.get("note") or "世界文件夹是空的"
        if result.get("truncated"):
            groups = "、".join(f"{k} {v}" for k, v in (result.get("dirs") or {}).items())
            root = "、".join(result.get("root_files") or [])
            return f"世界文件（{total} 个，按目录汇总）：" + (f"根目录：{root}；" if root else "") + groups
        shown = "、".join(files[:10]) + (f" 等{total}个" if total > 10 else "")
        return f"世界文件（{total} 个）：{shown}"

