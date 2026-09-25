"""file_edit — 编辑文件

增量编辑世界文件（查找替换/行后插入/删除行），比全量重写省 token。编辑前建议先 file_read 确认内容。多次插入时从最大行号开始往小插。
"""

from app.tools.world.base import WorldToolPlugin, WorldToolContext
from app.tools.world.shared import arg_error, lint_error
from app.utils.pure.file_edit import apply_file_edit, infer_operation  # 与主站共用同一份编辑核心（纯函数，无 IO，模块级导入即可）


class FileEditTool(WorldToolPlugin):
    name = 'file_edit'
    label = '编辑文件'
    segment = 'file'

    description = ('增量编辑世界文件（查找替换/行后插入/删除行），比全量重写省 token。编辑前建议先 file_read 确认内容。'
                   '落盘前对 .py/.js/.css 做语法自检（不通过会拒写并给行号；确认误报可加 skip_lint 强制写入）；'
                   '成功返回行数增减与首处改动摘要，多数情况不用再回读确认。多次插入时从最大行号开始往小插。')

    parameters = {'path': {'type': 'string', 'description': '相对路径'},
     'operation': {'type': 'string',
                   'enum': ['str_replace', 'insert', 'delete_lines'],
                   'description': '可省略：给了 old_string 就按 str_replace 处理。'
                                  'str_replace=精确替换（old_string 必须唯一）；insert=在 line '
                                  '行之后插入；delete_lines=删除 start_line..end_line（含两端）'},
     'old_string': {'type': 'string', 'description': 'str_replace 必填：被替换的精确原文'},
     'new_string': {'type': 'string', 'description': '替换后的新内容 / 要插入的内容'},
     'line': {'type': 'integer', 'description': 'insert 必填：在此行号之后插入（1 开头，0=文件开头）'},
     'start_line': {'type': 'integer', 'description': 'delete_lines 必填：起始行（1 开头）'},
     'end_line': {'type': 'integer', 'description': 'delete_lines 必填：结束行（含）'},
     'skip_lint': {'type': 'boolean', 'description': '仅当语法校验误报时用：跳过落盘前语法自检（默认 false）'}}

    # operation 不再必填：只给 old_string/new_string 时按 str_replace 处理（infer_operation 统一推断）
    required = ['path']

    async def execute(self, ctx: WorldToolContext) -> dict:
        try:
            args = ctx.args
            path = str(args.get("path", "")).strip()
            operation = infer_operation(args)
            if not path:
                return arg_error("缺少 path 参数", args)
            if operation not in ("str_replace", "insert", "delete_lines"):
                hint = ("只做查找替换时可以省略 operation（默认 str_replace）"
                        if not operation else f"收到 {operation!r}")
                return arg_error(f"operation 无法识别：{hint}；可选 str_replace / insert / delete_lines", args)
            from app.services.world.code_lint import lint_code
            from app.services.world.world_file_service import read_file, write_file
            from app.utils.pure.text_diff import summarize_change
            existing = read_file(ctx.world.id, path)
            if existing.get("binary"):
                return {"success": False, "error": "二进制文件不可编辑"}
            old_content = existing.get("content") or ""
            new_content, err = apply_file_edit(old_content, operation, args)
            if err:
                return {"success": False, "error": err}
            if not args.get("skip_lint"):
                problem = lint_code(path, new_content)
                if problem:
                    return lint_error(path, problem)
            write_file(ctx.world.id, path, new_content)
            # 落盘即回改动摘要：省掉模型回读全文确认的那一次调用
            return {"success": True, "path": path, "operation": operation,
                    **summarize_change(old_content, new_content)}
        except (ValueError, FileNotFoundError) as e:
            return {"success": False, "error": str(e)}

    def summary(self, result: dict) -> str:
        ok = bool(result.get("success"))
        if ok:
            op = {"str_replace": "替换", "insert": "插入", "delete_lines": "删除行"}.get(result.get("operation", ""), "编辑")
            return (f"已{op} {result.get('path')}"
                    f"（+{result.get('lines_added', 0)}/−{result.get('lines_removed', 0)} 行）")
        return f"编辑失败：{result.get('error', '未知错误')}"
