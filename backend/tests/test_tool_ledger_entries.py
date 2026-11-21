"""账本里的工具条目：只留失败与有持久副作用的结果。

读类调用成功只回一个 ok，进账本没有信息量；压缩时事件条目原样搬运，每轮一条会越积越长。
"""
from app.tools.base import ToolRegistry
from app.utils.pure.history import ledger_worthy_tools, tools_entry


def _item(name: str, note: str = "ok") -> dict:
    return {"name": name, "note": note}


def test_ok_reads_are_not_ledgered():
    items = [_item("bili_video"), _item("send_gm")]
    assert ledger_worthy_tools(items, ToolRegistry.has_persistent_effect) == []


def test_failures_keep_their_reason():
    kept = ledger_worthy_tools(
        [_item("bili_comment", "失败：B 站接口返回 -403")], ToolRegistry.has_persistent_effect,
    )
    assert [i["name"] for i in kept] == ["bili_comment"]
    assert "403" in tools_entry(kept)["content"]


def test_side_effect_tools_keep_their_result():
    for name in ("set_alarm", "update_alarm", "cancel_alarm", "store_memory", "forget_memory"):
        assert ToolRegistry.has_persistent_effect(name), name
        assert ledger_worthy_tools([_item(name)], ToolRegistry.has_persistent_effect), name


def test_read_only_tools_are_not_marked():
    for name in ("recall_memory", "list_alarms", "bili_video", "search_messages"):
        assert not ToolRegistry.has_persistent_effect(name), name
