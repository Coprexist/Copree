"""file_write — 写文件

创建或写入世界文件（HTML/CSS/JS/图片等，自动建目录，类型白名单限制）。创建网页/改代码用它。
"""

from app.tools.world.base import WorldToolPlugin, WorldToolContext
from app.tools.world.shared import arg_error, lint_error, attach_ref_warnings, ref_warning_hint


class FileWriteTool(WorldToolPlugin):
    name = 'file_write'
    label = '写文件'
    segment = 'file'

    description = ('创建或写入世界文件（HTML/CSS/JS/图片等，自动建目录，类型白名单限制）。创建网页/改代码用它。'
                   '落盘前对 .py/.js/.css 做语法自检（不通过会拒写并给行号；确认误报可加 skip_lint 强制写入）；'
                   '成功返回行数增减与带行号的改动行（-旧/+新），多数情况不用再 file_read 回读确认；'
                   '引用到的世界全局（UI.xxx / App.xxx）在当前世界找不到时，结果里带 warnings 提示。')

    parameters = {'path': {'type': 'string', 'description': '相对路径，如 index.html 或 css/style.css'},
     'content': {'type': 'string', 'description': '文件内容'},
     'skip_lint': {'type': 'boolean', 'description': '仅当语法校验误报时用：跳过落盘前语法自检（默认 false）'}}

    required = ['path', 'content']

    async def execute(self, ctx: WorldToolContext) -> dict:
        try:
            args = ctx.args
            path = str(args.get("path", "")).strip()
            content = str(args.get("content", ""))
            if not path:
                return arg_error("缺少 path 参数", args)
            from app.services.world.code_lint import lint_code
            from app.services.world.world_file_service import read_file, write_file
            from app.utils.pure.text_diff import summarize_change
            # 内容相同检测：打断模型的重复写入循环（温和提示，非硬拦截）
            old = None
            try:
                existing = read_file(ctx.world.id, path)
                if not existing.get("binary"):
                    old = existing.get("content") or ""
            except (ValueError, FileNotFoundError):
                pass  # 文件不存在 → 正常创建
            if old is not None and old == content:
                return {"success": True, "path": path, "unchanged": True, "note": "文件内容与现有完全一致，未做更改（无需重复写入）"}
            if not args.get("skip_lint"):
                problem = lint_code(path, content)
                if problem:
                    return lint_error(path, problem)
            write_file(ctx.world.id, path, content)
            # 落盘即回改动摘要：省掉模型回读全文确认的那一次调用
            return attach_ref_warnings(
                {"success": True, "path": path, "created": old is None, **summarize_change(old or "", content)},
                ctx.world.id, path, content)
        except (ValueError, FileNotFoundError) as e:
            return {"success": False, "error": str(e)}

    def summary(self, result: dict) -> str:
        ok = bool(result.get("success"))
        if ok:
            if result.get("skipped"):
                return f"⏭ 已跳过（该操作本次对话已执行过）：{result.get('path')}"
            if result.get("unchanged"):
                return f"未改动 {result.get('path')}（内容与现有完全一致）"
            if result.get("created"):
                return f"成功创建文件 {result.get('path')}（{result.get('lines_added', 0)} 行）" + ref_warning_hint(result)
            return (f"已写入 {result.get('path')}"
                    f"（+{result.get('lines_added', 0)}/−{result.get('lines_removed', 0)} 行）"
                    + ref_warning_hint(result))
        return f"创建文件失败：{result.get('error', '未知错误')}"
