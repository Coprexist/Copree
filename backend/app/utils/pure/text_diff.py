"""写入类工具的改动摘要（file_write / file_edit 共用）

世界 AI 改完文件后为了确认改对了，往往要回读一次（几千 token）。落盘时顺手回一个
「增减行数 + 改动处的前后行（带行号）」，多数情况就不用回读了。

为什么给带行号的 +/− 而不是「首处改动的后文摘录」：验收要看的是**换了什么**，不是改成后长什么样。
删掉一行/替换一行的场景里，只有后文摘录根本看不出删了什么（世界 AI 2026-10 反馈）。

"""

from __future__ import annotations

import difflib

__all__ = ["summarize_change"]

_MAX_LINES = 10        # 最多列几行（一次改一大块时按行数会很长）
_MAX_CHARS = 400       # 以及总字数上限（压缩过的一行也能上千字）
_ANCHOR_LINES = 2      # 两处改动之间留几行锚点（超过就折成省略号）


def summarize_change(old: str, new: str) -> dict:
    """返回 {lines_added, lines_removed, first_change_line, diff}；无差异时只有前两项 0。

    diff：带行号的改动行——删除 `-12|原文`、新增 `+12|新文`、锚点 ` 14|未变`，
    行号是**各自文件**里的行号；超预算截断并说清还有多少行没列。first_change_line 是新文件的 1 起行号。
    """
    old_lines = (old or "").splitlines()
    new_lines = (new or "").splitlines()
    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    opcodes = matcher.get_opcodes()
    added = removed = truncated = used = 0
    first_line: int | None = None
    rows: list[str] = []

    def add(row: str) -> None:
        nonlocal used, truncated
        if len(rows) >= _MAX_LINES or used + len(row) + 1 > _MAX_CHARS:
            truncated += 1
            return
        rows.append(row)
        used += len(row) + 1

    for idx, (tag, i1, i2, j1, j2) in enumerate(opcodes):
        if tag == "equal":
            # 只留两处改动之间的锚点：首尾两端不必留（省 token）
            if not 0 < idx < len(opcodes) - 1:
                continue
            if i2 - i1 > _ANCHOR_LINES:
                add("  …")
            else:
                for k in range(j1, j2):
                    add(f" {k + 1}|{new_lines[k]}")
            continue
        added += j2 - j1
        removed += i2 - i1
        if first_line is None:
            first_line = j1 + 1
        for k in range(i1, i2):
            add(f"-{k + 1}|{old_lines[k]}")
        for k in range(j1, j2):
            add(f"+{k + 1}|{new_lines[k]}")

    out = {"lines_added": added, "lines_removed": removed}
    if first_line is not None:
        out["first_change_line"] = first_line
        out["diff"] = "\n".join(rows) + (f"\n…（还有 {truncated} 行没列出）" if truncated else "")
    return out
