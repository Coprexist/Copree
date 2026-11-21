"""迁移 0109：同一段会话的重复帧并成一条。

规则与运行时归并（pure.merge_same_context）是同一张表，迁移里内联了一份快照——已经发布的
迁移不能跟着以后的重构改行为，所以这里直接读迁移文件里的实现来测。
"""
import importlib.util
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent


def _migration():
    path = BACKEND / "alembic" / "versions" / "0109_one_frame_per_context.py"
    spec = importlib.util.spec_from_file_location("migration_0109", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _frame(fid, ref, status, stamp, **extra):
    return {"id": fid, "type": "group_chat", "context_ref": ref, "status": status,
            "created_at": stamp, "last_active_at": stamp, **extra}


def test_duplicates_merge_into_the_live_frame_and_leave():
    """同会话的重复帧并进在跑的那条：环境锁、便签副本、待告知都跟过去，空壳被摘掉。"""
    mod = _migration()
    locked = {"channel": "qq", "member_num": 21}
    stack = [
        _frame("old1", "group:66", "ended", "2026-11-01T00:00:00+00:00",
               env_locked=locked, env_notified=locked),
        _frame("old2", "group:66", "ended", "2026-11-03T00:00:00+00:00",
               notes=[{"id": "n1"}], call_count=2),
        _frame("live", "group:66", "active", "2026-11-09T00:00:00+00:00", call_count=3),
        _frame("other", "group:67", "ended", "2026-11-02T00:00:00+00:00"),
    ]

    merged = mod._merge_duplicates(stack)

    assert [f["id"] for f in merged] == ["old1", "old2"], "被并的是那两条空壳"
    assert [f["id"] for f in stack] == ["live", "other"], "只留幸存者，别的会话不动"
    keeper = stack[0]
    assert keeper["env_locked"] == locked, "锁留在旧帧上时要并到现帧"
    assert keeper["env_notified"] == locked
    assert keeper["notes"] == [{"id": "n1"}]
    assert keeper["call_count"] == 5, "调用次数是求和，不是覆盖"
    assert keeper["last_active_at"] == "2026-11-09T00:00:00+00:00"
    assert all(f["merged_into"] == "live" for f in merged), "被并帧留痕，迁移据此改指针"


def test_keeper_is_the_active_frame_even_if_it_is_older():
    """幸存者按身份挑（当前帧优先），不按时间挑：当前帧换个时间戳也不该被它自己的副本顶掉。"""
    mod = _migration()
    stack = [
        _frame("live", "dm:12", "active", "2026-11-01T00:00:00+00:00", doing="在私信里回话"),
        _frame("ghost", "dm:12", "ended", "2026-11-09T00:00:00+00:00"),
    ]

    merged = mod._merge_duplicates(stack)

    assert [f["id"] for f in stack] == ["live"]
    assert stack[0]["doing"] == "在私信里回话"
    assert [f["id"] for f in merged] == ["ghost"]


def test_metadata_markers_stay_with_the_merged_frame():
    """retired_at / merged_into 不搬：标记只属于被并的那条，幸存者不该继承"""
    mod = _migration()
    stack = [
        _frame("old", "group:9", "ended", "2026-11-01T00:00:00+00:00",
               doing="旧的", merged_into="older", retired_at="2026-11-02T00:00:00+00:00"),
        _frame("live", "group:9", "active", "2026-11-03T00:00:00+00:00"),
    ]

    merged = mod._merge_duplicates(stack)

    assert [f["id"] for f in stack] == ["live"]
    assert "merged_into" not in stack[0] and "retired_at" not in stack[0]
    assert [f["id"] for f in merged] == ["old"]
    assert merged[0]["merged_into"] == "live", "留痕改记真正的幸存者"


def test_merging_twice_changes_nothing():
    """迁移可重跑：第二次没有重复帧可并，数组一个字节都不动。"""
    mod = _migration()
    stack = [
        _frame("a", "group:1", "ended", "2026-11-01T00:00:00+00:00", tail=["早说过了"]),
        _frame("b", "group:1", "active", "2026-11-02T00:00:00+00:00"),
    ]
    mod._merge_duplicates(stack)
    before = [dict(f) for f in stack]

    assert mod._merge_duplicates(stack) == []
    assert stack == before


def test_one_conversation_one_frame_keeps_distinct_conversations():
    """不同会话之间不串：group:66 与 group:67 各留一条。"""
    mod = _migration()
    stack = [
        _frame("a", "group:66", "ended", "2026-11-01T00:00:00+00:00"),
        _frame("b", "group:66", "active", "2026-11-02T00:00:00+00:00"),
        _frame("c", "group:67", "active", "2026-11-02T00:00:00+00:00"),
        _frame("d", "", "ended", "2026-11-02T00:00:00+00:00"),   # 没有会话归属的帧不参与
    ]

    mod._merge_duplicates(stack)

    assert [f["id"] for f in stack] == ["b", "c", "d"]
