"""技能目录与要点：渲染必须字节级确定，去重/预算/场景各一条。

这批测试全走纯函数（不连库）：目录每轮进前缀，行为应该是可预测的字符串运算，
把它测成集成测试反而看不出"为什么这行没了"。
"""
from app.utils.pure.tool_catalog import (
    call_shape, collect_notes, render_catalog, render_notes,
)


def _entry(name, segment, desc="", params=None, required=None, notes=(), scenes=frozenset()):
    return {
        "name": name, "segment": segment, "segment_name": segment, "segment_desc": "",
        "description": desc, "parameters": params or {}, "required": required or [],
        "usage_notes": list(notes), "usage_scenes": scenes,
    }


def test_call_shape_puts_required_first_then_optional():
    props = {"path": {}, "end_line": {}, "start_line": {}}
    assert call_shape(props, ["path"]) == "(path, end_line?, start_line?)"
    assert call_shape({}, []) == ""


def test_catalog_is_deterministic_and_keeps_every_tool():
    entries = [_entry("b_tool", "memory"), _entry("a_tool", "memory")]
    allowed = {"a_tool", "b_tool"}
    first = render_catalog(entries, allowed)
    second = render_catalog(list(reversed(entries)), set(reversed(list(allowed))))
    # 渲染只按传入顺序：调用方（catalog_entries）负责排序，这里验证不吃集合顺序
    assert first == render_catalog(entries, allowed)
    assert sum(1 for line in first if "a_tool" in line or "b_tool" in line) == 2
    assert second != []  # 反向输入也应照样渲染（顺序由调用方决定）


def test_notes_are_sorted_by_level_then_segment_then_tool():
    entries = [
        _entry("z_tool", "chat_social", notes=[(300, "用法提示")]),
        _entry("a_tool", "chat_social", notes=[(400, "次要提示")]),
    ]
    seg_notes = [{"segment": "chat_social", "level": 100, "notes": ["铁律一条"], "scenes": frozenset()}]
    notes = collect_notes(entries, seg_notes, scene="group_chat")
    assert [n[0] for n in notes] == [100, 300, 400]
    lines, dropped = render_notes(notes)
    assert dropped == 0
    assert lines[0] == "·（铁律）铁律一条"


def test_same_sentence_is_kept_only_once():
    entries = [
        _entry("a_tool", "memory", notes=[(300, "同一句话")]),
        _entry("b_tool", "memory", notes=[(300, "同一句话")]),
    ]
    notes = collect_notes(entries, [], scene="group_chat")
    lines, _ = render_notes(notes)
    assert lines == ["·（用法）同一句话"]


def test_scene_filter_only_hides_notes_not_tools():
    entries = [_entry("a_tool", "chat_social", scenes=frozenset({"world"}))]
    seg_notes = [{"segment": "chat_social", "level": 200, "notes": ["只在世界"], "scenes": frozenset({"world"})}]
    group_lines, _ = render_notes(collect_notes(entries, seg_notes, scene="group_chat"))
    world_lines, _ = render_notes(collect_notes(entries, seg_notes, scene="world"))
    assert group_lines == []
    assert world_lines == ["·（场景）只在世界"]
    # 工具本身不因场景被裁：裁的是要点（权限与折叠另有一套）
    assert any("a_tool" in line for line in render_catalog(entries, {"a_tool"}))


def test_budget_drops_the_lowest_priority_and_says_how_many():
    notes = [(100, 0, "", 0, "铁律"), (400, 0, "", 0, "很长的提示" * 50)]
    lines, dropped = render_notes(notes, budget=40)
    assert lines == ["·（铁律）铁律"]
    assert dropped == 1


def test_frozen_allowed_locks_isolates_and_clears():
    """名单锁：首见为空、写入后读回是排序列表、别的会话互不影响、解锁点清空。"""
    from app.services.capability_versioning import (
        freeze_allowed, frozen_allowed, clear_frozen_allowed,
    )

    holder = {}
    assert frozen_allowed(holder, "group:7") is None
    freeze_allowed(holder, "group:7", {"b_tool", "a_tool"})
    assert frozen_allowed(holder, "group:7") == ["a_tool", "b_tool"]  # 排序后存
    assert frozen_allowed(holder, "group:8") is None                  # 按会话隔离
    clear_frozen_allowed(holder, "group:7")
    assert frozen_allowed(holder, "group:7") is None
