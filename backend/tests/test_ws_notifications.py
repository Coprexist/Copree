"""站内通知与多连接连接池契约测试（零网络、零数据库）

三件事：
1. 同一个人可以同时拿会话连接与常驻通知连接（多个标签页也一样），互相不顶掉；
2. 会话消息只推给"订阅了该会话"的常驻连接（push 事件），typing 这类噪音不推；
3. 通知范围断开就清掉，坏连接自动摘除。

跑法：docker exec -w /app ai_group_backend python tests/run_without_pytest.py test_ws_notifications
"""
from app.services.connection_manager import ConnectionManager


class FakeWS:
    """够用的假连接：只记下发出去的帧，可选模拟已断开"""

    def __init__(self, fail: bool = False):
        self.sent: list[dict] = []
        self.fail = fail

    async def send_json(self, payload: dict) -> None:
        if self.fail:
            raise RuntimeError("连接已断开")
        self.sent.append(payload)

    def pushes(self) -> list[dict]:
        return [p for p in self.sent if p.get("type") == "push"]

    def connected(self, mgr: ConnectionManager, user_id: int) -> bool:
        return any(self in sockets for sockets in mgr.user_connections.get(user_id, set())) or \
            any(self in sockets for sockets in mgr.notification_connections.get(user_id, set()))


async def test_two_sockets_of_one_user_both_receive():
    """同一用户两条连接（标签页 / 会话+通知）不再互相顶掉"""
    mgr = ConnectionManager()
    a, b = FakeWS(), FakeWS()
    await mgr.connect(a, 7, 1)
    await mgr.connect(b, 7, 1)

    await mgr.broadcast_to_group(7, {"type": "message", "data": {"content": "hi"}})
    assert len(a.sent) == 1 and len(b.sent) == 1, "两条连接都该收到"

    await mgr.send_to_user(1, {"type": "friend_notification", "data": {}})
    assert len(a.sent) == 2 and len(b.sent) == 2, "用户级推送也该两条都到"


async def test_notification_socket_gets_group_message_push():
    """常驻通知连接没订阅会话，也能收到该群消息的 push（弹窗靠它）"""
    mgr = ConnectionManager()
    notifier = FakeWS()
    await mgr.subscribe_notifications(notifier, 2, group_ids=[7], session_ids=[])

    await mgr.broadcast_to_group(7, {"type": "message", "data": {"content": "在吗"}})
    pushes = notifier.pushes()
    assert len(pushes) == 1, f"应有一条 push，实际 {notifier.sent}"
    data = pushes[0]["data"]
    assert data["kind"] == "group_message" and data["conversation_id"] == 7
    assert data["message"]["content"] == "在吗"

    # 没订阅的群：不推
    await mgr.broadcast_to_group(8, {"type": "message", "data": {"content": "别的群"}})
    assert len(notifier.pushes()) == 1


async def test_dm_message_push_reaches_notifier():
    """私信同理：对方没订阅这个会话，常驻连接也能收到 push"""
    mgr = ConnectionManager()
    notifier = FakeWS()
    await mgr.subscribe_notifications(notifier, 2, group_ids=[], session_ids=["1_2"])

    await mgr.broadcast_to_dm("1_2", {"type": "message", "data": {"content": "私聊"}})
    pushes = notifier.pushes()
    assert len(pushes) == 1
    assert pushes[0]["data"]["kind"] == "dm_message" and pushes[0]["data"]["conversation_id"] == "1_2"


async def test_noise_events_are_not_pushed():
    """typing / 上线状态这类噪音不该弹窗——不然打字就弹通知"""
    mgr = ConnectionManager()
    notifier = FakeWS()
    await mgr.subscribe_notifications(notifier, 2, group_ids=[7], session_ids=[])

    for noise in ({"type": "typing"}, {"type": "user_online"}, {"type": "state_change"}, {"type": "read"}):
        await mgr.broadcast_to_group(7, noise)
    assert notifier.pushes() == [], f"噪音不该推：{notifier.sent}"


async def test_sender_is_not_pushed_own_message():
    """自己发的消息不给自己弹窗"""
    mgr = ConnectionManager()
    notifier = FakeWS()
    await mgr.subscribe_notifications(notifier, 1, group_ids=[7], session_ids=[])

    await mgr.broadcast_to_group(7, {"type": "message", "data": {}}, exclude_user_id=1)
    assert notifier.pushes() == []


async def test_disconnect_clears_scope_and_presence():
    """断开后范围清掉、在线状态跟着消失，后续广播不再打扰"""
    mgr = ConnectionManager()
    notifier = FakeWS()
    await mgr.subscribe_notifications(notifier, 2, group_ids=[7], session_ids=["1_2"])
    assert mgr.is_user_online(2) is True

    mgr.disconnect_all(notifier, 2)
    assert mgr.notification_scopes.get(2) is None, "范围要跟着连接一起清"
    assert mgr.is_user_online(2) is False

    await mgr.broadcast_to_group(7, {"type": "message", "data": {}})
    assert notifier.pushes() == []


async def test_broadcast_to_all_and_send_to_user_reach_notifier():
    """维护模式广播、好友/邀请通知都要能落到常驻通知连接（弹窗另一条来源）"""
    mgr = ConnectionManager()
    notifier = FakeWS()
    await mgr.subscribe_notifications(notifier, 2, group_ids=[], session_ids=[])
    assert mgr.is_user_online(2) is True, "开着站内通知连接就算在线"

    await mgr.broadcast_to_all({"type": "maintenance_update", "mode": "soft", "msg": "维护中"})
    await mgr.send_to_user(2, {"type": "friend_notification", "data": {"event": "request_accepted"}})
    assert [p["type"] for p in notifier.sent] == ["maintenance_update", "friend_notification"]


async def test_dead_socket_is_dropped():
    """推送失败要摘掉坏连接，别一直往死连接上写"""
    mgr = ConnectionManager()
    dead = FakeWS(fail=True)
    await mgr.connect(dead, 7, 1)

    await mgr.broadcast_to_group(7, {"type": "message", "data": {}})
    assert mgr.group_connections.get(7, {}).get(1) is None
    assert mgr.is_user_online(1) is False

async def test_human_dm_route_pushes_to_peer():
    """人类发的私信必须推给对方（源码级守卫）。

    历史空缺：AI 回复有自己的 broadcast，人类发的私信一条推送都没有，
    对方只能靠轮询等出来——站内弹窗也就永远弹不出来。
    """
    import inspect

    from app.routers import dm as dm_router

    source = inspect.getsource(dm_router.send_dm)
    assert "broadcast_to_dm" in source, "人类私信路由缺少对外推送"
    assert "exclude_user_id" in source, "推送要排除发送者，否则自己收自己的消息"


async def test_notification_subscribe_is_handle_in_ws_loop():
    """ws 循环必须认识 notifications_subscribe：漏了就等于常驻连接白连"""
    import inspect

    from app.routers import ws as ws_router

    source = inspect.getsource(ws_router.websocket_endpoint)
    assert "notifications_subscribe" in source
    assert "subscribe_notifications" in source


async def test_mention_push_carries_preview_and_flag(migrated_db):
    """弹窗要带两样东西：人话正文（@ 换人名、去 Markdown）与"这条点到谁"

    正文与收件人无关，只整理一次；点名逐人算，且两种点名分开放：
    mentioned_me 只认"点到你个人"（浮窗据此破免打扰），mentions_all 认 @all（不破）。
    """
    from sqlalchemy import text

    from app.database import async_session

    async with async_session() as db:
        from db_reset import clear
        await clear(db, "external_identities", "plugin_service_states", "plugin_configs", "plugins",
                    "pending_messages", "messages", "dm_messages", "dm_sessions", "group_members",
                    "groups", "users", "agents")
        await db.execute(text(
            "INSERT INTO users (id, username, password_hash, type) VALUES "
            "(2, '小明', 'x', 'human'), (3, '小红', 'x', 'human')"
        ))
        await db.commit()

    mgr = ConnectionManager()
    me, other = FakeWS(), FakeWS()
    await mgr.subscribe_notifications(me, 2, group_ids=[7], session_ids=[])
    await mgr.subscribe_notifications(other, 3, group_ids=[7], session_ids=[])

    original = "**重要** <@!2> 看一下"
    await mgr.broadcast_to_group(7, {"type": "message", "data": {"content": original}})
    mine, theirs = me.pushes()[0]["data"], other.pushes()[0]["data"]
    assert mine["preview"] == "重要 @小明 看一下", mine["preview"]
    assert mine["message"]["content"] == original, "浮窗正文是给人看的另一版，推送里的原文不能被动过"
    assert mine["mentioned_me"] is True and theirs["mentioned_me"] is False
    assert mine["mentions_all"] is False and theirs["mentions_all"] is False

    # @all 是"喊所有人"：mentions_all 置位，但**不算点到我个人**——它也就不破免打扰
    await mgr.broadcast_to_group(7, {"type": "message", "data": {"content": "@all 开会了"}})
    for push in (me.pushes()[1]["data"], other.pushes()[1]["data"]):
        assert push["mentions_all"] is True
        assert push["mentioned_me"] is False, "@all 不该被当成'点名到个人'"
    # @ai 是叫 AI：人两种都不算
    await mgr.broadcast_to_group(7, {"type": "message", "data": {"content": "@ai 帮忙看下"}})
    assert me.pushes()[2]["data"]["mentioned_me"] is False
    assert me.pushes()[2]["data"]["mentions_all"] is False

    # 纯附件：正文要给占位，别弹出一条空白的
    await mgr.broadcast_to_group(7, {"type": "message", "data": {
        "content": "", "attachments": [{"mime_type": "image/png", "name": "a.png"}],
    }})
    assert me.pushes()[3]["data"]["preview"] == "[图片]", me.pushes()[3]["data"]["preview"]


async def test_no_recipient_means_no_payload_work():
    """没人开着浮窗时不该为通知做任何加工（正文整理要查库，这条在消息广播链路上）"""
    mgr = ConnectionManager()
    notifier = FakeWS()
    await mgr.subscribe_notifications(notifier, 2, group_ids=[7], session_ids=[])
    # 别的群没有订阅者：不该炸、也不该推给任何人
    await mgr.broadcast_to_group(8, {"type": "message", "data": {"content": "<@!2> 在吗"}})
    assert notifier.pushes() == []


