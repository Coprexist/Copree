"""
文件增量编辑核心（纯函数，无 IO）— 主站 file_edit 与世界 edit_world_file 共用一份实现

语义（与主站一致）：
- str_replace：精确字符串替换，old_string 必须存在且唯一，否则拒绝
- insert：在 insert_line（1 开头）行之后插入；0 = 文件开头；多次插入从最大行号往小插
- delete_lines：删除 start_line..end_line（1 开头，含两端）
"""


def infer_operation(arguments: dict) -> str:
    """operation 的缺省推断（唯一口径，主站与世界工具共用）。

    模型只给 old_string/new_string（最常见的"改一处"）时会漏写 operation，
    此时按参数形状补出意图，而不是直接打回让模型重来一次
    （世界 AI 2026-09-21 反馈：这一漏一打回白耗一轮）。
    返回空串表示无法推断，由调用方给出报错。
    """
    explicit = str(arguments.get("operation") or "").strip()
    if explicit:
        return explicit
    if arguments.get("old_string") is not None:
        return "str_replace"
    if arguments.get("insert_line") is not None or arguments.get("line") is not None:
        return "insert"
    if arguments.get("start_line") is not None or arguments.get("end_line") is not None:
        return "delete_lines"
    return ""


def apply_file_edit(old_content: str, operation: str, arguments: dict) -> tuple[str | None, str | None]:
    """应用一次增量编辑。成功返回 (新内容, None)；失败返回 (None, 错误信息)。"""
    new_string = str(arguments.get("new_string", ""))
    lines = old_content.split("\n")

    if operation == "str_replace":
        old_string = str(arguments.get("old_string", ""))
        if not old_string:
            return None, "str_replace 操作需要提供 old_string 参数"
        count = old_content.count(old_string)
        if count == 0:
            return None, "未在文件中找到匹配的原文。请检查 old_string 是否完全一致（含缩进和换行）。"
        if count > 1:
            return None, f"在文件中找到 {count} 处匹配，无法确定替换哪一处。请提供更长的上下文确保唯一。"
        return old_content.replace(old_string, new_string, 1), None

    if operation == "insert":
        insert_line = int(arguments.get("insert_line", arguments.get("line", 0)) or 0)
        if insert_line < 0:
            return None, "insert_line 不能小于 0（0 = 文件开头）"
        if insert_line > len(lines):
            return None, f"行号 {insert_line} 超出文件范围（共 {len(lines)} 行）"
        new_lines = lines.copy()
        # insert_line=N → 在第 N 行之后插入（1 开头）；0 → 文件开头
        new_lines.insert(insert_line, new_string)
        return "\n".join(new_lines), None

    if operation == "delete_lines":
        start_line = int(arguments.get("start_line", 1) or 1)
        end_line = int(arguments.get("end_line", 1) or 1)
        if start_line < 1 or end_line < 1:
            return None, "start_line 和 end_line 从 1 开始"
        if start_line > end_line:
            return None, "start_line 不能大于 end_line"
        if start_line > len(lines):
            return None, f"start_line {start_line} 超出文件范围（共 {len(lines)} 行）"
        s = max(0, start_line - 1)
        e = min(len(lines), end_line)  # end_line 含，slice 用 e（不包含）
        if s >= len(lines):
            return None, "起始行超出文件范围"
        del lines[s:e]
        return "\n".join(lines), None

    return None, f"不支持的操作: {operation}"
