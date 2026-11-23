"""清测试表：删「外键闭包」，代替 TRUNCATE ... CASCADE。

为什么值得单开一个工具（本机存储实测）：
    TRUNCATE users CASCADE              4163 ms / 次   （闭包 51 张表）
    DELETE 同样的 51 张表 + 15 张       13 ms / 次
TRUNCATE 要给闭包里每张表换 relfilenode（建文件 + 写 WAL + 目录 fsync），在这台机器的
存储上就是几百毫秒一张，跟表里有没有数据无关；DELETE 是行级删除，闭包里的表基本是空的，
几乎不花时间。两者清掉的是**同一组表**（TRUNCATE CASCADE 的范围就是外键闭包），
所以语义一致；测试里的 id 都是显式写死的，也不需要 TRUNCATE 的额外效果。

DELETE 期间把 session_replication_role 临时调成 replica：不用操心删除顺序。
（0128 之后指向 users/agents 的外键都带了 ON DELETE 语义，但其它表之间的 NO ACTION
外键仍然存在，闭包里删表的顺序照样不保证，所以这里不开 replica 不行。）
删完立刻调回 default —— 后面的 INSERT 仍然要正常做外键检查，别把测试的"脏数据"放过去。
"""
from sqlalchemy import text

# 外键闭包：从 roots 出发，反复找"引用了闭包里某张表"的表
_CLOSURE_SQL = """
WITH RECURSIVE refs(relid) AS (
    SELECT c.oid
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relname IN ({roots})
    UNION
    SELECT con.conrelid
    FROM pg_constraint con JOIN refs r ON con.confrelid = r.relid
    WHERE con.contype = 'f'
)
SELECT relid::regclass::text FROM refs
"""


_TEST_DB_SUFFIXES = ("_test",)


async def _assert_test_db(db) -> str:
    """删库前先认库名：只允许 *_test。

    2026-11-05 的事故：一个探针脚本只设了 `TEST_DATABASE_URL`，而 `app.database` 读的是
    `DATABASE_URL`——两个环境变量都指向同一个引擎，漏设一个就落到生产库上，
    `clear("users", "agents", ...)` 按外键闭包一次清掉 53 张表。
    保护放在这里而不是调用点：只要想删，就必须先过这道名。
    """
    name = (await db.execute(text("SELECT current_database()"))).scalar_one()
    if not str(name).endswith(_TEST_DB_SUFFIXES):
        raise RuntimeError(
            f"拒绝清库：当前库是 {name!r}，不是测试库。"
            f"这个助手会删掉外键闭包里的全部数据（{_TEST_DB_SUFFIXES} 才放行）——"
            "先确认 DATABASE_URL/TEST_DATABASE_URL 指向测试库")
    return str(name)


async def clear(db, *roots: str) -> None:
    """清空 roots 及其外键闭包里的全部表（等价 TRUNCATE roots CASCADE，但快三个数量级）。

    只在测试库上可用：库名不以 `_test` 结尾一律拒绝（见 `_assert_test_db`）。
    """
    if not roots:
        return
    await _assert_test_db(db)
    roots_sql = ", ".join("'" + r.replace("'", "") + "'" for r in roots)
    names = [row[0] for row in (await db.execute(text(_CLOSURE_SQL.format(roots=roots_sql)))).all()]
    await db.execute(text("SET LOCAL session_replication_role = replica"))
    for name in names:
        await db.execute(text(f"DELETE FROM {name}"))
    await db.execute(text("SET LOCAL session_replication_role = DEFAULT"))
