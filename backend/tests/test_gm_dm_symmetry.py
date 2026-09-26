"""群聊（GM）与私信（DM）的接口命名对称。

约定见 docs/guides/用户手册.md：GM = Group Message，DM = Direct Message。
REST 侧同形：/gm/{group_id}/messages ↔ /dm/{session_id}/messages。

历史事故：群消息一度有三个入口（/chat/message、/chat/messages、
/groups/{id}/messages）。其中 /chat/* 无认证，且 POST 恒 500 —— ChatApi
签名缺 dm_session_id 参数。本用例锁住"群消息只有一个入口"。
"""
from app.main import app

GROUP_MESSAGE_PATH = "/gm/{group_id}/messages"
DM_MESSAGE_PATH = "/dm/{session_id}/messages"

# 已删除的重复入口：重新出现即视为回归
REMOVED_PATHS = (
    "/chat/message",
    "/chat/messages",
    "/groups/{group_id}/messages",
    "/chat/group/dnd",
    "/chat/group/{group_id}",
    "/chat/group/{group_id}/members",
    "/chat/group/join",
    "/chat/group/leave",
)


def _route_table() -> dict[str, set[str]]:
    table: dict[str, set[str]] = {}
    for route in app.routes:
        path = getattr(route, "path", None)
        if not path:
            continue
        table.setdefault(path, set()).update(getattr(route, "methods", None) or ())
    return table


async def test_gm_and_dm_message_endpoints_are_symmetric():
    table = _route_table()
    for path in (GROUP_MESSAGE_PATH, DM_MESSAGE_PATH):
        assert path in table, f"{path} 未注册"
        methods = table[path]
        assert {"GET", "POST"} <= methods, f"{path} 应同时提供 GET/POST，实际 {sorted(methods)}"


async def test_duplicate_group_message_entrypoints_stay_removed():
    table = _route_table()
    for path in REMOVED_PATHS:
        assert path not in table, f"{path} 是已删除的重复入口，不得回归"


async def test_user_and_friend_routes_are_kept():
    """chat 前缀下剩下的是用户与好友查询，不属群聊语义，按约定保留。"""
    table = _route_table()
    assert "/chat/user/{user_id}" in table
    assert "/chat/friend/request" in table

async def test_group_message_payload_is_the_serializer_shape(migrated_db):
    """REST 群消息的字段集合必须等于 message_serializer 的产物（与 WS 推送同形）。

    事故（2026-09-26）：该接口曾声明 response_model，等同于手抄第二份字段清单；
    FastAPI 只返回清单内的字段，其余被静默丢弃——刷新后「来自 QQ」标签消失、
    撤回消息显示为空内容（via / revoked / sender_state 同时丢失）。字段清单只应有一份。
    """
    import httpx
    from sqlalchemy import select, text

    from app.database import async_session, get_db
    from app.models.message import Message
    from app.routers.deps import require_group_member
    from app.utils.message_serializer import serialize_message

    async with async_session() as db:
        from db_reset import clear

        await clear(db, "messages", "group_members", "groups", "agents", "users")
        await db.execute(text(
            "INSERT INTO users (id, username, password_hash, type) VALUES "
            "(1, '群主', 'x', 'human'), (90, 'QQ 用户', 'x', 'external')"
        ))
        await db.execute(text(
            "INSERT INTO groups (id, name, owner_type, owner_id, avatar_mode, include_ai_in_avatar) "
            "VALUES (64, '群', 'human', 1, 'default', true)"
        ))
        await db.execute(text(
            "INSERT INTO group_members (group_id, member_type, member_id, role) VALUES "
            "(64, 'human', 1, 'owner')"
        ))
        # 外部通道来的人落库就是 sender_type='human' + via='qq'（真机 1414/1424 如此）
        await db.execute(text(
            "INSERT INTO messages (group_id, sender_type, sender_id, content, via, created_at) VALUES "
            "(64, 'human', 90, 'QQ 来的', 'qq', '2026-09-26 04:00:00'), "
            "(64, 'human', 90, '被撤了', null, '2026-09-26 04:01:00')"
        ))
        await db.execute(text("UPDATE messages SET revoked_at = now() WHERE content = '被撤了'"))
        await db.commit()
        rows = (await db.execute(select(Message).order_by(Message.id))).scalars().all()
        expected_keys = set(serialize_message(rows[0]).keys())

    async def _test_db():
        async with async_session() as session:
            yield session

    app.dependency_overrides[get_db] = _test_db
    app.dependency_overrides[require_group_member] = lambda: {"user_id": 1}
    try:
        async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/gm/64/messages", params={"limit": 20})
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200, resp.text
    quoted, revoked = resp.json()
    assert quoted["via"] == "qq", "via 决定「来自 QQ」标签，不得被响应模型过滤"
    assert revoked["revoked"] is True and revoked["content"] == "", "撤回仅下发标记，不下发原文"
    assert set(quoted.keys()) == expected_keys, "REST 返回的字段集合必须与 message_serializer 一致"

