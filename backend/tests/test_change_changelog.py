"""变更通知里的"改变量"：文本源行级 diff，工具源字段级差异

通知的全部意义是"以新的为准"，而它得先看得见新的是什么——只报"更新能力 X"等于没说：
工具数组要等解锁才换，这条通知是 AI 当下唯一的准信（世界 AI 原话：「不只费 token，
我会去找不存在的工具、白烧轮次」）。
"""
import pytest

pytestmark = pytest.mark.anyio

_TEXT_PAD = "（补白）" * 30        # 超过 60 字才走行级 diff 分支（短文本给「旧 → 新」更清楚）


def _tool(name: str, description: str, properties: dict, required: list) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required}}}


def _text(value: str) -> list:
    return [{"type": "text", "content": value}]


async def test_long_text_change_is_a_line_diff():
    from app.services.capability_versioning import _diff_changelog

    out = _diff_changelog(_text("第一行\n第二行\n第三行" + _TEXT_PAD),
                          _text("第一行\n第二行（改过）\n第三行" + _TEXT_PAD))
    assert "- 第二行" in out and "+ 第二行（改过）" in out, out
    assert "首处差异" not in out


async def test_short_text_change_gives_old_to_new():
    from app.services.capability_versioning import _diff_changelog

    assert _diff_changelog(_text("小傻福"), _text("群视界机器人")) == "内容更新：「小傻福」 → 「群视界机器人」"


async def test_line_diff_truncates_and_says_how_many_are_left():
    from app.services.capability_versioning import _line_diff

    out = _line_diff("\n".join(f"旧{i}" for i in range(200)),
                     "\n".join(f"新{i}" for i in range(200)), budget=120)
    assert out.startswith("- 旧0") and "还有" in out and "行没列出" in out, out


async def test_new_tool_gives_the_whole_definition():
    """新能力要给全：它眼前的工具数组里根本没有这个能力（要等解锁才换）"""
    from app.services.capability_versioning import _diff_changelog

    out = _diff_changelog([], [_tool("forget_memory", "删掉一条记忆",
                                     {"memory_id": {"type": "integer", "description": "要删的 id"}},
                                     ["memory_id"])])
    assert "新增能力 forget_memory" in out
    assert "说明：删掉一条记忆" in out
    assert "参数 memory_id（integer）：要删的 id" in out
    assert "必填：memory_id" in out


async def test_removed_tool_says_do_not_call_it():
    from app.services.capability_versioning import _diff_changelog

    out = _diff_changelog([_tool("old_tool", "旧能力", {}, [])], [])
    assert "移除能力 old_tool" in out and "不要再调用它" in out, out


async def test_changed_tool_lists_every_field_level_change():
    """说明、参数增删、参数说明、类型、必填——模型按这些调工具，逐条给"""
    from app.services.capability_versioning import _diff_changelog

    old = _tool("store_memory", "存储一条记忆", {
        "title": {"type": "string", "description": "标题"},
        "weight": {"type": "integer", "description": "权值"},
    }, ["title"])
    new = _tool("store_memory", "存储一条记忆；带 memory_id 则改已有的那条", {
        "memory_id": {"type": "integer", "description": "要改的那条 id"},
        "title": {"type": "string", "description": "记忆标题"},
        "weight": {"type": "number", "description": "权值 1-5"},
    }, ["title", "memory_id"])

    out = _diff_changelog([old], [new])
    assert "更新能力 store_memory" in out
    assert "说明：「存储一条记忆」 → 「存储一条记忆；带 memory_id 则改已有的那条」" in out, out
    assert "新增参数 memory_id（integer）：要改的那条 id" in out
    assert "参数 title 说明：「标题」 → 「记忆标题」" in out
    assert "参数 weight 类型：integer → number" in out
    assert "参数 weight 说明：「权值」 → 「权值 1-5」" in out
    assert "必填项：title → title、memory_id" in out


async def test_removed_parameter_is_called_out():
    from app.services.capability_versioning import _diff_changelog

    old = _tool("t", "说明", {"a": {"type": "string", "description": "甲"},
                              "b": {"type": "string", "description": "乙"}}, ["a"])
    new = _tool("t", "说明", {"a": {"type": "string", "description": "甲"}}, ["a"])
    out = _diff_changelog([old], [new])
    assert "去掉参数 b" in out, out


async def test_long_tool_description_walks_the_same_diff_path():
    from app.services.capability_versioning import _diff_changelog

    old = _tool("t", "第一行说明\n第二行说明" + _TEXT_PAD, {}, [])
    new = _tool("t", "第一行说明\n第二行说明（改过）" + _TEXT_PAD, {}, [])
    out = _diff_changelog([old], [new])
    assert "说明：\n- 第二行说明" in out and "+ 第二行说明（改过）" in out, out


async def test_tool_diff_is_budgeted():
    from app.services.capability_versioning import _diff_changelog

    props = {f"p{i}": {"type": "string", "description": f"参数{i}" + "说明" * 20} for i in range(40)}
    out = _diff_changelog([_tool("t", "说明", {}, [])], [_tool("t", "说明", props, [])])
    assert "这个能力的差异过长，已截断" in out, out[-200:]
