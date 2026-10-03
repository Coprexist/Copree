"""两个列表接口的查询条数不随会话数增长（N+1 回归闸）。

实测过的代价：25 个群 177 条 SQL、32 条私信 141 条 SQL，而侧栏每次刷新、每条消息的
未读计数都要跑它们。这两个数字一旦回涨，就是有人又写回了「每个会话查一轮」。
"""
from sqlalchemy import event, text


async def _seed(n_groups: int, n_sessions: int) -> None:
    from app.database import async_session

    async with async_session() as db:
        from db_reset import clear
        await clear(db, "users", "groups", "dm_sessions")
        await db.execute(text(
            "INSERT INTO users (id, username, password_hash, type, role, is_active) VALUES "
            "(1, '我', 'x', 'human', 'user', true), "
            "(2, '别人', 'x', 'human', 'user', true), "
            "(3, '某个AI', 'x', 'ai', 'user', true)"
        ))
        for i in range(n_groups):
            gid = 100 + i
            await db.execute(text(
                "INSERT INTO groups (id, name, owner_type, owner_id, avatar_mode, include_ai_in_avatar) "
                "VALUES (:g, :n, 'human', 1, 'default', true)"
            ), {"g": gid, "n": f"群{i}"})
            # 入群时间早于消息，未读才数得出来（now() 是事务时间，两边同时写就相等了）
            await db.execute(text(
                "INSERT INTO group_members (group_id, member_type, member_id, role, joined_at) VALUES "
                "(:g, 'human', 1, 'owner', now() - interval '1 hour'), "
                "(:g, 'ai', 3, 'member', now() - interval '1 hour')"
            ), {"g": gid})
            await db.execute(text(
                "INSERT INTO messages (group_id, sender_type, sender_id, content, created_at) "
                "VALUES (:g, 'human', 2, '喂 @我', now())"
            ), {"g": gid})
        for i in range(n_sessions):
            uid = 10 + i
            sid = f"1_{uid}"
            await db.execute(text(
                "INSERT INTO users (id, username, password_hash, type, role, is_active) "
                "VALUES (:i, :n, 'x', 'human', 'user', true)"
            ), {"i": uid, "n": f"人{i}"})
            await db.execute(text(
                "INSERT INTO dm_sessions (session_id, user1_id, user2_id, last_message_at) "
                "VALUES (:s, 1, :u, now())"
            ), {"s": sid, "u": uid})
            await db.execute(text(
                "INSERT INTO dm_messages (session_id, sender_id, content, message_type, read_at, created_at) "
                "VALUES (:s, :u, '在吗 @我', 'normal', NULL, now())"
            ), {"s": sid, "u": uid})
            await db.execute(text(
                "UPDATE dm_sessions SET last_message_id = "
                "(SELECT max(id) FROM dm_messages WHERE session_id = :s) WHERE session_id = :s"
            ), {"s": sid})
        await db.commit()


async def _count_queries(fn, *args):
    """跑一次 fn，返回（SQL 条数, 结果）"""
    from app.database import async_session, engine

    seen = []
    def _on(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)
    event.listen(engine.sync_engine, "before_cursor_execute", _on)
    try:
        async with async_session() as db:
            out = await fn(db, *args)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _on)
    return len(seen), out


async def test_group_list_queries_do_not_grow_with_groups(migrated_db):
    from app.chat.gm import list_user_groups

    await _seed(3, 0)
    n3, out3 = await _count_queries(list_user_groups, 1)
    await _seed(12, 0)
    n12, out12 = await _count_queries(list_user_groups, 1)

    assert len(out3) == 3 and len(out12) == 12
    assert out12[0]["unread_count"] == 1 and out12[0]["has_mention"] is True
    assert out12[0]["last_message_preview"] == "别人: 喂 @我", out12[0]["last_message_preview"]
    assert n12 == n3, f"群从 3 个加到 12 个，SQL 从 {n3} 变成 {n12}——又写回 N+1 了"
    assert n12 <= 12, f"一轮列表跑了 {n12} 条 SQL，太多了"


async def test_dm_list_queries_do_not_grow_with_sessions(migrated_db):
    from app.chat.dm import list_dm_sessions

    await _seed(0, 3)
    n3, out3 = await _count_queries(list_dm_sessions, 1)
    await _seed(0, 12)
    n12, out12 = await _count_queries(list_dm_sessions, 1)

    assert len(out3) == 3 and len(out12) == 12
    assert out12[0]["partner"]["name"] == "人0", out12[0]["partner"]
    assert out12[0]["unread_count"] == 1 and out12[0]["last_message_preview"] == "在吗 @我"
    assert n12 == n3, f"会话从 3 条加到 12 条，SQL 从 {n3} 变成 {n12}——又写回 N+1 了"
    assert n12 <= 10, f"一轮列表跑了 {n12} 条 SQL，太多了"
