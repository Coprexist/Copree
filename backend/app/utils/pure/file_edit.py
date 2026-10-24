"""
文件增量编辑核心（纯函数，无 IO）— 主站 file_edit 与世界 edit_world_file 共用一份实现

语义（与主站一致）：
- str_replace：精确字符串替换，old_string 必须存在；默认要求唯一，多处匹配时用 occurrence 指定
  （occurrence=1 起第 N 处 / 0 = 全部替换）——坐标只差缩进的两行靠 occurrence 定位，
  不必再往上贴更长上下文
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


def parse_occurrence(value) -> int | None:
    """occurrence 归一：None/空 = 未指定（沿用「必须唯一」的安全默认）；0 或 "all" = 全部替换；其余按第 N 处。

    世界 AI 2026-10 反馈：两行只差两个空格缩进时被判成「2 处匹配」，它只能往上贴更多上下文绕。
    给出显式坐标后，这一来一回就省掉了。
    """
    if value in (None, ""):
        return None
    if isinstance(value, str) and value.strip().lower() in ("all", "*"):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def match_lines(content: str, needle: str, cap: int = 5) -> list[int]:
    """每次命中所在的 1 起行号（报错时列出来，模型才能直接改用 occurrence 定位）。"""
    out: list[int] = []
    idx = content.find(needle)
    while idx >= 0 and len(out) < cap:
        out.append(content.count("\n", 0, idx) + 1)
        idx = content.find(needle, idx + 1)
    return out


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
        occurrence = parse_occurrence(arguments.get("occurrence"))
        if count > 1 and occurrence is None:
            lines_hit = match_lines(old_content, old_string)
            where = "、".join(f"第 {n} 行" for n in lines_hit) + (" 等" if count > len(lines_hit) else "")
            return None, (f"在文件中找到 {count} 处匹配（{where}），无法确定替换哪一处："
                          "occurrence=N 指定第 N 处（1 起），occurrence=0 全部替换；"
                          "只有缩进不同时也可以补上缩进让上下文唯一。")
        if occurrence == 0:
            return old_content.replace(old_string, new_string), None
        if occurrence is not None and not 1 <= occurrence <= count:
            return None, f"occurrence={occurrence} 超出范围（文件里共有 {count} 处匹配）"
        nth = occurrence or 1
        idx = -1
        for _ in range(nth):
            idx = old_content.find(old_string, idx + 1)
        return old_content[:idx] + new_string + old_content[idx + len(old_string):], None

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
