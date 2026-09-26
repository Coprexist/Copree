"""库表与时间的契约守卫

两件过去靠人盯、结果盯漏的事，落成用例：

1. 时间戳列的时区约定：全库一律「无时区 UTC」（PG 的 timestamp without time zone）。
   存量 7 个带时区列列在白名单里当欠债清单；新增或还清都会在这里体现。
   为什么较真：aware 值写进无时区列会被 asyncpg 直接拒绝（invalid input for query
   argument），而 naive 与 aware 相减会 TypeError —— 见
   docs/memory_system/design/focus_and_memory_reach.md。
2. 迁移编号：id 全库唯一、只有一个 head、数字编号连续不跳号。
   2026-09-26 出过事：新迁移取了与既有文件相同的 id，alembic 判为多 head，容器起不来。
"""
import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent

# 存量带时区列（历史欠债；还清一个就从名单里删掉一个）
AWARE_COLUMNS = {
    "agent_alarms.wake_at",
    "agent_alarms.fired_at",
    "structured_records.created_at",
    "structured_records.updated_at",
    "users.last_active_at",
    "verification_codes.expires_at",
    "verification_codes.created_at",
}


def test_timestamp_columns_stay_timezone_naive():
    import app.models  # noqa: F401  注册全部模型
    from sqlalchemy import DateTime

    from app.database import Base

    found = {f"{t.name}.{c.name}"
             for t in Base.metadata.tables.values()
             for c in t.columns
             if isinstance(c.type, DateTime) and c.type.timezone}

    assert found == AWARE_COLUMNS, (
        "带时区的时间戳列变了：新增的请改成无时区并用 utc_now 落库，已还清的请从名单删掉。"
        f"多出来 {sorted(found - AWARE_COLUMNS)}；已还清 {sorted(AWARE_COLUMNS - found)}"
    )


def test_no_deprecated_utcnow_left():
    """datetime.utcnow() 已废弃；时间一律走 app.utils.pure.timeutil 的唯一入口。"""
    offenders = [p.relative_to(BACKEND).as_posix()
                 for p in (BACKEND / "app").rglob("*.py")
                 if "datetime.utcnow()" in p.read_text(encoding="utf-8")]

    assert not offenders, f"请改用 app.utils.pure.timeutil.utc_now：{offenders}"


def _migrations() -> list[tuple[str, str, list[str]]]:
    """(文件名, revision, down_revision 列表)。"""
    out = []
    for path in sorted((BACKEND / "alembic" / "versions").glob("*.py")):
        text = path.read_text(encoding="utf-8")
        rev = re.search(r"^revision[^=]*=\s*['\"]([^'\"]+)['\"]", text, re.M)
        down = re.search(r"^down_revision[^=]*=\s*(.+)$", text, re.M)
        if not rev:
            continue
        ids = re.findall(r"['\"]([^'\"]+)['\"]", down.group(1)) if down else []
        out.append((path.name, rev.group(1), ids))
    return out


def test_migration_ids_are_unique_with_a_single_head():
    rows = _migrations()
    ids = [r for _, r, _ in rows]

    dupes = {i for i in ids if ids.count(i) > 1}
    assert not dupes, f"迁移 id 重复：{sorted(dupes)}（撞 id 会让 alembic 判多 head）"

    referenced = {d for _, _, downs in rows for d in downs}
    heads = [r for r in ids if r not in referenced]
    assert len(heads) == 1, f"迁移图必须只有一个 head，实际 {heads}"


def test_ordered_migration_ids_are_contiguous():
    """数字编号（新约定）必须连续不跳号：跳号说明中间那一步被漏掉了。"""
    numbered = sorted(int(r) for _, r, _ in _migrations() if r.isdigit())

    assert numbered, "至少应有一个数字编号的迁移"
    assert numbered == list(range(numbered[0], numbered[0] + len(numbered))), (
        f"数字编号不连续：{numbered}"
    )


def test_ordered_migration_filename_matches_revision():
    """新约定的迁移：文件名 = 编号_描述（改 id 忘了改文件名时，排查会多绕一圈）。"""
    for name, rev, _ in _migrations():
        if rev.isdigit():
            assert name.startswith(f"{rev}_"), f"{name} 的文件名前缀与 revision {rev} 不一致"
