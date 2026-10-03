"""私信「标记已读」的唯一入口 —— 贴底时前端主动同步走的就是它。

未读的真相只有 dm_messages.read_at 一处；拉消息、拉会话详情、贴在底部时的主动同步
三条路径共用 mark_dm_read。这里守两件事：
1. 「已读」只吃对方发来的那条（自己发的 read_at 记的是"对方看没看"，不能动）；
2. 不拉消息只标已读，未读数要真的跟着变——侧边栏红数泡就靠这个。
"""
import httpx
from sqlalchemy import text


async def _seed():
    from app.database import async_session

    async with async_session() as db:
        from db_reset import clear
        await clear(db, "dm_messages", "dm_sessions", "users")
        await db.execute(text(
            "INSERT INTO users (id, username, password_hash, type, role, is_active) VALUES "
            "(1, '人类', 'x', 'human', 'user', true), "
            "(41, '浮生', 'x', 'ai', 'user', true), "
            "(99, '路人', 'x', 'human', 'user', true)"
        ))
        await db.execute(text(
            "INSERT INTO dm_sessions (session_id, user1_id, user2_id) VALUES ('1_41', 1, 41)"
        ))
        await db.execute(text(
            "INSERT INTO dm_messages (session_id, sender_id, content, message_type, read_at, created_at) VALUES "
            "('1_41', 1, '你早', 'normal', NULL, '2026-10-03 10:00:00'), "
            "('1_41', 41, '早', 'normal', NULL, '2026-10-03 10:01:00'), "
            "('1_41', 41, '在忙吗', 'normal', NULL, '2026-10-03 10:02:00')"
        ))
        await db.commit()


def _client(ip: str) -> httpx.AsyncClient:
    from app.main import app

    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=(ip, 123)), base_url="http://test"
    )


def _token(user_id: int, name: str) -> dict:
    from app.utils.auth import create_access_token

    return {"Authorization": "Bearer " + create_access_token(
        {"user_id": user_id, "username": name, "role": "user"})}


async def test_marks_only_the_peers_messages(migrated_db):
    """自己发的那条不动：read_at 在那个方向记的是「对方看没看」"""
    from app.chat.dm import mark_dm_read
    from app.database import async_session

    await _seed()
    async with async_session() as db:
        assert await mark_dm_read(db, "1_41", 1) == 2, "浮生发来的两条"
        await db.commit()

    async with async_session() as db:
        rows = (await db.execute(text(
            "SELECT sender_id, read_at FROM dm_messages ORDER BY id"
        ))).all()
    assert [r[1] is None for r in rows] == [True, False, False], rows


async def test_second_call_is_a_no_op(migrated_db):
    """前端贴底后每次滚动/来消息都会调一次，重复调用不能出错也不能重复计数"""
    from app.chat.dm import mark_dm_read
    from app.database import async_session

    await _seed()
    async with async_session() as db:
        assert await mark_dm_read(db, "1_41", 1) == 2
        assert await mark_dm_read(db, "1_41", 1) == 0
        await db.commit()


async def test_outsider_is_rejected(migrated_db):
    """不是会话双方就不能标已读——否则谁都能把别人的未读抹了"""
    from app.chat.dm import mark_dm_read
    from app.database import async_session

    await _seed()
    async with async_session() as db:
        # 容器里的跑器是自带的轻量版（没有 pytest.raises），手动接住
        for session_id, user_id in (("1_41", 99), ("不存在", 1)):
            try:
                await mark_dm_read(db, session_id, user_id)
            except ValueError:
                continue
            raise AssertionError(f"{session_id} / {user_id} 不该被放行")


async def test_reading_messages_still_marks_read(migrated_db):
    """拉消息顺带标已读这条老行为不变（三条路径合并成一个 UPDATE 之后）"""
    from app.chat.dm import get_dm_messages
    from app.database import async_session

    await _seed()
    async with async_session() as db:
        await get_dm_messages(db, "1_41", 1)
        await db.commit()

    async with async_session() as db:
        left = (await db.execute(text(
            "SELECT count(*) FROM dm_messages WHERE sender_id != 1 AND read_at IS NULL"
        ))).scalar()
    assert left == 0


async def test_endpoint_clears_the_sidebar_badge(migrated_db):
    """贴底时前端只发这一个请求：红数泡的来源（/dm/sessions 的 unread_count）要归零"""
    await _seed()

    async with _client("198.18.0.31") as client:
        before = await client.get("/dm/sessions", headers=_token(1, "人类"))
        assert before.status_code == 200, before.text
        assert before.json()[0]["unread_count"] == 2, before.text

        read = await client.post("/dm/1_41/read", headers=_token(1, "人类"))
        assert read.status_code == 200 and read.json()["read_count"] == 2, read.text

        after = await client.get("/dm/sessions", headers=_token(1, "人类"))
        assert after.json()[0]["unread_count"] == 0, after.text

        # 对方那一侧不受影响：人类发的那条还没被读
        peer = await client.get("/dm/sessions", headers=_token(41, "浮生"))
        assert peer.json()[0]["unread_count"] == 1, peer.text


async def test_endpoint_rejects_outsiders(migrated_db):
    await _seed()

    async with _client("198.18.0.32") as client:
        r = await client.post("/dm/1_41/read", headers=_token(99, "路人"))
    assert r.status_code == 400 and "无权" in r.json()["detail"], r.text
