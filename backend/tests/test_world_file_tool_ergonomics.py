"""世界文件与提示词的几道摩擦力（世界 AI 2026-10 反馈，都来自它真踩到的轮次）。

每条反馈钉一条契约，防止以后回退：

1. `str_replace` 多处匹配（只差两个空格缩进的两行）不是死路——报错必须给行号，并支持 `occurrence`；
2. 改动摘要要让「换了什么」看得见：带行号的 −/+，而不是「改成后长什么样」的摘录；
3. 正则写不成 Python 正则时（如 JS 的 `\\x{1F300}`）不许静默退化成子串——码点转义自动换算，
   真退化时必须带说明（否则 0 命中会被读成「文件里没有」）；
4. 根目录文件（index.html / main.py）不许被目录汇总藏成一个计数；
5. 跨文件导出名与调用名对不上（`UI.emptyBox`）要当场提示——只提示不拦；
6. 收尾清单不许再把 AI 支去找不存在的 world_push；收尾两步怎么记账要写在预算段里。
"""
from __future__ import annotations

import contextlib
import shutil

from app.services.world import world_file_service as fs

WORLD_ID = 987651

# 正则退化成普通子串时，结果里必须出现这个字段
FALLBACK_KEY = "pattern_note"


class _World:
    """世界替身：这些工具只读 world.id"""

    def __init__(self, world_id: int = WORLD_ID):
        self.id = world_id


@contextlib.contextmanager
def _sandbox_world_dir():
    """用这个世界的目录，用完清掉（数据根在 conftest 里已指向临时目录）"""
    from app.paths import world_dir

    d = world_dir(WORLD_ID)
    d.mkdir(parents=True, exist_ok=True)
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_str_replace_lists_match_lines_then_takes_occurrence():
    """缩进只差两个空格的两行：报错给行号，occurrence 一指定就能改"""
    from app.utils.pure.file_edit import apply_file_edit

    content = "function a() {\n  return 星尘;\n}\nfunction b() {\n    return 星尘;\n}\n"
    args = {"old_string": "return 星尘;", "new_string": "return 星尘2;"}

    _, err = apply_file_edit(content, "str_replace", args)
    assert err and "2 处匹配" in err and "第 2 行" in err and "第 5 行" in err, err
    assert "occurrence" in err

    new, err = apply_file_edit(content, "str_replace", {**args, "occurrence": 2})
    assert err is None
    assert "  return 星尘;\n" in new and "    return 星尘2;\n" in new      # 只动了第 5 行那处

    new, err = apply_file_edit(content, "str_replace", {**args, "occurrence": 0})
    assert err is None and new.count("星尘2") == 2                         # 0 = 全部替换

    _, err = apply_file_edit(content, "str_replace", {**args, "occurrence": 9})
    assert err and "超出范围" in err


def test_change_summary_shows_what_changed_not_what_remains():
    """摘要要是带行号的 −/+：替换掉一行时，「改成后长什么样」看不出删了什么"""
    from app.utils.pure.text_diff import summarize_change

    out = summarize_change("a\nb\nc\n", "a\nB\nc\n")
    assert out["lines_added"] == 1 and out["lines_removed"] == 1
    assert "-2|b" in out["diff"] and "+2|B" in out["diff"]
    assert "excerpt" not in out, "旧的「后文摘录」字段已被 diff 取代"
    assert summarize_change("", "x\n")["diff"] == "+1|x"       # 新文件只给 +


def test_grep_normalizes_codepoint_escapes_and_never_degrades_silently():
    """JS 的 `\\x{1F300}`：自动换算成码点；真编译不了就按子串搜并说明"""
    with _sandbox_world_dir():
        fs.write_file(WORLD_ID, "js/a.js", 'var s = "🎮 开始";\nvar t = "plain";\n')

        result = fs.grep_paths(WORLD_ID, ["js/a.js"], "[\\x{1F300}-\\x{1FAFF}]")
        assert len(result["hits"]) == 1, result                   # 换算成码点后能搜到 emoji
        assert FALLBACK_KEY not in result

        result = fs.grep_paths(WORLD_ID, ["js/a.js"], "[unclosed")
        assert result["hits"] == []
        assert "普通子串" in result.get(FALLBACK_KEY, ""), result


async def test_root_files_survive_truncation():
    """超过 50 个文件时只给目录汇总，但根目录文件照样列名字"""
    from app.tools.world.base import WorldToolContext
    from app.tools.world.file_list import FileListTool

    with _sandbox_world_dir():
        for i in range(55):
            fs.write_file(WORLD_ID, f"data/cards/c{i}.json", "{}")
        fs.write_file(WORLD_ID, "index.html", "<html></html>")
        fs.write_file(WORLD_ID, "main.py", "print(1)")

        ctx = WorldToolContext(world_repo=None, world=_World(), arguments="{}", args={})
        result = await FileListTool().execute(ctx)

        assert result["truncated"] is True
        assert result["root_files"] == ["index.html", "main.py"]
        assert "(根目录)" not in (result.get("dirs") or {})


def test_ref_warnings_catch_export_name_mismatch():
    """js/ui.js 导出 empty，js/views/lobby.js 调 emptyBox → 当场提示（双向：改导出方也报）"""
    from app.services.world.code_refs import ref_warnings

    with _sandbox_world_dir():
        ui = ('(function () {\n  var UI = window.UI || (window.UI = {});\n'
              '  function emptyBox(t) { return t; }\n'
              '  window.UI = { h: h, empty: emptyBox };\n})();\n')
        lobby = "(function () { return UI.emptyBox('空'); })();\n"
        fs.write_file(WORLD_ID, "js/ui.js", ui)
        fs.write_file(WORLD_ID, "js/views/lobby.js", lobby)

        for path, text in (("js/views/lobby.js", lobby), ("js/ui.js", ui)):
            warnings = ref_warnings(WORLD_ID, path, text)
            assert len(warnings) == 1 and "UI.emptyBox" in warnings[0], (path, warnings)
            assert "js/views/lobby.js:1" in warnings[0]

    # 动态挂载（展开 / defineProperty）判不了，一律不下结论
    with _sandbox_world_dir():
        fs.write_file(WORLD_ID, "js/dyn.js", "window.Dyn = { ...base };\n")
        fs.write_file(WORLD_ID, "js/use.js", "Dyn.whatever();\n")
        assert ref_warnings(WORLD_ID, "js/use.js", "Dyn.whatever();\n") == []


async def test_file_write_attaches_ref_warnings():
    """工具层要真的把提示带出来（不是只有服务层能算）"""
    from app.tools.world.base import WorldToolContext
    from app.tools.world.file_write import FileWriteTool

    with _sandbox_world_dir():
        fs.write_file(WORLD_ID, "js/views/lobby.js", "UI.h();\nUI.emptyBox('空');\n")
        fs.write_file(WORLD_ID, "js/ui.js", "window.UI = { h: h, empty: emptyBox };\n")
        content = "window.UI = { h: h };\n"          # 少了 empty：调用方立刻断
        
        ctx = WorldToolContext(world_repo=None, world=_World(), arguments="",
                               args={"path": "js/ui.js", "content": content})
        result = await FileWriteTool().execute(ctx)

        assert result["success"] is True
        assert result["warnings"] and "UI.emptyBox" in result["warnings"][0], result
        assert "引用存疑" in FileWriteTool().summary(result)


def test_closing_checklist_no_longer_points_at_world_push():
    """收尾清单①曾让 AI「本地镜像改完要 world_push 同步回世界」——本环境没有这一步"""
    from app.services.world.world_chat_service import build_forced_prompt, build_budget_prompt

    text = build_forced_prompt()
    start = text.index("【收尾清单】")
    segment = text[start:text.index("【", start + 1)]
    assert "world_push" not in segment, segment
    assert "落盘即生效" in segment

    budget = build_budget_prompt(50)
    assert "收尾的账" in budget, budget
    assert "manage_records" in budget and "run_world_code" in budget
