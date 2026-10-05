"""work_session 的提交语义：出块即提交 / 抛错回滚 / 嵌套**不是 savepoint**

这三条是契约（后台轮次与 HTTP 都靠它）。后两个用例把两种嵌套结局都钉下来：
内层回滚不影响外层 / 内层提交连外层回滚也带不走。
"""
import pytest
from sqlalchemy import text

pytestmark = pytest.mark.anyio

_PROBE = "CREATE TABLE IF NOT EXISTS _ws_probe (id int primary key, note text)"


async def _reset(db):
    """探针表：只在测试库里用，各用例开头清空（create 要提交，独立会话才看得见它）"""
    await db.execute(text(_PROBE))
    await db.execute(text("DELETE FROM _ws_probe"))
    await db.commit()


async def _rows(db):
    return [tuple(r) for r in (await db.execute(text("SELECT id, note FROM _ws_probe ORDER BY id"))).all()]


async def test_block_exit_commits(migrated_db):
    """出块即提交：块外另开一个会话就看得到"""
    from app.database import async_session, work_session

    async with work_session() as db:
        await _reset(db)
        await db.execute(text("INSERT INTO _ws_probe (id, note) VALUES (1, 'A')"))

    async with async_session() as db:
        assert await _rows(db) == [(1, "A")]


async def test_error_rolls_back(migrated_db):
    """抛错回滚：这一段一个字都不许留下（"发出去了但没落库"正是要防的）"""
    from app.database import async_session, work_session

    async with work_session() as db:
        await _reset(db)
        await db.execute(text("INSERT INTO _ws_probe (id, note) VALUES (1, 'A')"))

    # 不用 pytest.raises：容器里的最小运行器只实现了 fixture 与 mark（见 tests/run_without_pytest.py）
    raised = False
    try:
        async with work_session() as db:
            await db.execute(text("INSERT INTO _ws_probe (id, note) VALUES (2, 'B')"))
            raise RuntimeError("这一段没干成")
    except RuntimeError:
        raised = True
    assert raised, "work_session 把异常吞了：必须往上抛"

    async with async_session() as db:
        assert await _rows(db) == [(1, "A")]


async def test_nested_inner_rollback_does_not_touch_outer(migrated_db):
    """嵌套不是 savepoint（一）：内层是独立事务，它自己回滚不影响外层的账"""
    from app.database import async_session, work_session

    async with work_session() as outer:
        await _reset(outer)
        await outer.execute(text("INSERT INTO _ws_probe (id, note) VALUES (1, '外层')"))
        try:
            async with work_session() as inner:
                await inner.execute(text("INSERT INTO _ws_probe (id, note) VALUES (2, '内层')"))
                raise RuntimeError("内层没干成")
        except RuntimeError:
            pass

    async with async_session() as db:
        assert await _rows(db) == [(1, "外层")]


async def test_nested_inner_commit_survives_outer_rollback(migrated_db):
    """嵌套不是 savepoint（二）：内层提交是**真的提交**——外层随后回滚带不走它。

    所以需要"一起成、一起败"的地方不能靠嵌套，必须共用一个 session（本仓惯例：函数收 db 参数）。
    """
    from app.database import async_session, work_session

    raised = False
    try:
        async with work_session() as outer:
            await _reset(outer)
            async with work_session() as inner:
                await inner.execute(text("INSERT INTO _ws_probe (id, note) VALUES (1, '内层已提交')"))
            await outer.execute(text("INSERT INTO _ws_probe (id, note) VALUES (2, '外层未提交')"))
            raise RuntimeError("外层没干成")
    except RuntimeError:
        raised = True
    assert raised, "外层那段该抛的异常没抛出来"

    async with async_session() as db:
        assert await _rows(db) == [(1, "内层已提交")], "外层回滚只带走它自己写的那行"
