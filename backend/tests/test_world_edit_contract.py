"""file_edit 的 operation 缺省 与 世界工具去重 的契约守卫（世界 AI 2026-09-21 反馈）。

两条被反馈的摩擦各自钉一条规则：
1. 只给 old_string/new_string 时必须按 str_replace 走，不许打回让模型重来；
2. 去重只对**成功**结果生效——失败的重试永远放行（拦下来只会白耗一轮）。
"""
from __future__ import annotations

from app.utils.pure.file_edit import apply_file_edit, infer_operation


def test_infer_operation_by_argument_shape():
    assert infer_operation({}) == ""
    assert infer_operation({"old_string": "a"}) == "str_replace"
    assert infer_operation({"line": 3, "new_string": "b"}) == "insert"
    assert infer_operation({"start_line": 1, "end_line": 2}) == "delete_lines"
    # 显式写的优先，哪怕参数形状像别的操作
    assert infer_operation({"operation": "insert", "old_string": "a", "line": 1}) == "insert"
    assert infer_operation({"operation": "  "}) == ""


def test_str_replace_without_operation_applies():
    new_content, err = apply_file_edit("a\nb\nc\n", infer_operation({"old_string": "b", "new_string": "B"}), {"old_string": "b", "new_string": "B"})
    assert err is None and new_content == "a\nB\nc\n"


async def _drive_dedup(results):
    """用假 run_world_tool 驱动两次相同调用，返回两次结果。

    写成 async：官方跑法（tests/run_without_pytest.py）本身就在事件循环里，
    同步用例里再 asyncio.run() 会直接 RuntimeError。
    """
    from app.tools import world as world_mod

    calls = {"n": 0}

    async def fake_run(*args, **kwargs):
        calls["n"] += 1
        return results[min(calls["n"] - 1, len(results) - 1)]

    original = world_mod.run_world_tool
    world_mod.run_world_tool = fake_run
    try:
        turn_state: dict = {}

        async def scenario():
            first = await world_mod.execute_world_tool(None, None, "edit_world_file", '{"path":"a.js"}', turn_state)
            second = await world_mod.execute_world_tool(None, None, "edit_world_file", '{"path":"a.js"}', turn_state)
            return first, second

        return await scenario(), calls["n"]
    finally:
        world_mod.run_world_tool = original


async def test_failed_call_is_never_deduped():
    (first, second), calls = await _drive_dedup([{"success": False, "error": "未在文件中找到匹配的原文"}])
    assert not first.get("skipped") and not second.get("skipped")
    assert calls == 2, "失败的重复调用必须真的再执行一次"


async def test_identical_success_is_still_deduped():
    (first, second), calls = await _drive_dedup([{"success": True, "path": "a.js"}])
    assert not first.get("skipped")
    assert second.get("skipped") is True
    assert calls == 2


async def test_world_tool_can_reach_the_shared_core():
    """世界 file_edit 必须真的调到共享编辑核心。

    回归：曾把 from app.utils.pure.file_edit import infer_operation 写在 execute 内部，
    而函数开头就先用它 —— 该 import 让 infer_operation 变成整个函数的局部名，
    于是每次调用都是 UnboundLocalError（世界 #45 日志里刷了几十条）。
    这里用假的世界文件服务真跑一遍 execute，把「能不能调到」钉死。
    """
    import sys
    import types

    from app.tools.world.file_edit import FileEditTool

    written: dict = {}
    fake_files = types.ModuleType("app.services.world.world_file_service")
    fake_files.read_file = lambda world_id, path: {"content": "a\n"}
    fake_files.write_file = lambda world_id, path, content: written.update(content=content)

    class _World:
        id = 45

    class _Ctx:
        args = {"path": "a.txt", "old_string": "a", "new_string": "b"}
        world = _World()

    real = sys.modules.get("app.services.world.world_file_service")
    sys.modules["app.services.world.world_file_service"] = fake_files
    try:
        result = await FileEditTool().execute(_Ctx())
    finally:
        if real is None:
            sys.modules.pop("app.services.world.world_file_service", None)
        else:
            sys.modules["app.services.world.world_file_service"] = real

    assert result.get("success") is True, result
    assert written["content"] == "b\n"
