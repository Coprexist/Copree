"""QQ 通道插件：入站进 Copree、出站回 QQ、多实例、私聊策略、出口隔离

不起网关、不连外网——只测"消息怎么走"这一段（那是这个插件的全部逻辑所在）：
QQ 事件 → Copree（与网页端同一条投递链路）→ AI 回复 → 回到发消息的那个 QQ 群/用户。
"""
import asyncio
import importlib.util
from pathlib import Path

from sqlalchemy import text

PLUGIN_PATH = Path(__file__).resolve().parents[1] / "plugins" / "qq-channel" / "plugin.py"
GROUP_ID = 5
GROUP_OWNER, AGENT_USER = 1, 2


def _load_plugin_module():
    """按插件的加载方式导入（set_current_plugin 让 @service 认得 owner）"""
    from app.services.plugin import api

    spec = importlib.util.spec_from_file_location("_test_qq_channel", str(PLUGIN_PATH))
    module = importlib.util.module_from_spec(spec)
    api.set_current_plugin("qq-channel")
    try:
        spec.loader.exec_module(module)
    finally:
        api.set_current_plugin(None)
    return module


class FakeClient:
    """假 QQ 客户端：只记录发出去什么"""

    def __init__(self):
        self.sent = []

    async def send_group(self, group_openid, content, msg_id=None, msg_seq=1):
        self.sent.append({"kind": "group", "target": group_openid, "content": content,
                          "msg_id": msg_id, "seq": msg_seq})
        return {"id": "fake"}

    async def send_c2c(self, user_openid, content, msg_id=None, msg_seq=1):
        self.sent.append({"kind": "dm", "target": user_openid, "content": content,
                          "msg_id": msg_id, "seq": msg_seq})
        return {"id": "fake"}


async def _seed():
    from app.database import async_session

    async with async_session() as db:
        await db.execute(text(
            "TRUNCATE external_identities, plugin_service_states, plugin_configs, plugins, "
            "pending_messages, messages, dm_messages, dm_sessions, group_members, groups, users, agents CASCADE"
        ))
        # 下面用写死的 id 播种，序列要往前挪，否则插件新建用户会撞上 id=1/2
        for table in ("users", "agents", "groups", "messages", "dm_messages"):
            await db.execute(text(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), 100)"))
        await db.execute(text(
            "INSERT INTO users (id, username, password_hash, type) VALUES "
            f"({GROUP_OWNER}, '群主', 'x', 'human'), ({AGENT_USER}, '浮生', 'x', 'ai')"
        ))
        await db.execute(text(
            f"INSERT INTO agents (id, owner_id, name, user_id, discoverable) VALUES (7, {GROUP_OWNER}, '浮生', {AGENT_USER}, true)"
        ))
        await db.execute(text(
            f"INSERT INTO groups (id, name, owner_type, owner_id, avatar_mode, include_ai_in_avatar) "
            f"VALUES ({GROUP_ID}, 'QQ 测试群', 'human', {GROUP_OWNER}, 'default', true)"
        ))
        await db.execute(text(
            f"INSERT INTO group_members (group_id, member_type, member_id, role) VALUES "
            f"({GROUP_ID}, 'human', {GROUP_OWNER}, 'owner'), ({GROUP_ID}, 'ai', {AGENT_USER}, 'member')"
        ))
        await db.commit()


async def _make_plugin(instance: str = "bot-a"):
    """造一个"已启动"的插件实例：start() 里的两件事——注册出口、起后台任务——这里手工等价完成"""
    from app.chat.outbound import register_sink

    module = _load_plugin_module()
    plugin = module.QqChannelPlugin()
    plugin.id = "qq-channel"
    plugin.instance = instance
    plugin._copree_group_id = GROUP_ID
    plugin._target_agent = "浮生"
    plugin._target_user_id = AGENT_USER
    plugin._client = FakeClient()
    register_sink(plugin.id, group=plugin._outbound_sink, dm=plugin._dm_outbound_sink)

    async def _alive():
        await asyncio.sleep(3600)

    plugin._task = asyncio.create_task(_alive())
    return plugin


def _cleanup(plugin):
    from app.chat.outbound import unregister_sink

    unregister_sink(plugin.id)
    plugin._task.cancel()


async def _wait_sent(plugin, timeout=0.5):
    for _ in range(int(timeout / 0.01)):
        if plugin._client.sent:
            return
        await asyncio.sleep(0.01)


async def _messages(group_id: int):
    from app.database import async_session

    async with async_session() as db:
        rows = (await db.execute(text(
            "SELECT m.sender_type, m.sender_id, m.content, u.username, m.via "
            "FROM messages m LEFT JOIN users u ON u.id = m.sender_id "
            "WHERE m.group_id = :g ORDER BY m.id"
        ), {"g": group_id})).all()
    return rows


GROUP_EVENT = {
    "id": "MSG-1",
    "group_openid": "QQGROUP-AAA",
    "content": "今天天气不错",
    "author": {"member_openid": "OPENID-XYZ", "username": "小明"},
    "attachments": [],
}

DM_EVENT = {
    "id": "DMMSG-1",
    "content": "在吗",
    "author": {"user_openid": "OPENID-DM", "username": "小红"},
    "attachments": [],
}


async def test_group_round_trip(migrated_db):
    from app.chat.gm import send_gm_message
    from app.database import async_session

    await _seed()
    plugin = await _make_plugin()
    try:
        await plugin._on_group_at(dict(GROUP_EVENT))

        # 最近见过的 QQ 群要回报给卡片：白名单该怎么填，必须先看得见群 openid
        status = await plugin.get_status()
        assert [g["openid"] for g in status["recent_groups"]] == ["QQGROUP-AAA"], status["recent_groups"]
        assert status["recent_groups"][0]["count"] == 1 and status["recent_groups"][0]["allowed"] is True

        # 白名单把群挡下时也要记账（allowed=False），否则用户永远发现不了这个群
        plugin._allow = {"SOME-OTHER-GROUP"}
        await plugin._on_group_at({**GROUP_EVENT, "id": "MSG-2", "group_openid": "QQGROUP-BBB"})
        status = await plugin.get_status()
        blocked = {g["openid"]: g for g in status["recent_groups"]}["QQGROUP-BBB"]
        assert blocked["allowed"] is False, blocked
        assert len(await _messages(GROUP_ID)) == 1, "被白名单挡下的群不该进 Copree"

        rows = await _messages(GROUP_ID)
        assert len(rows) == 1, rows
        sender_type, _sender_id, content, username, via = rows[0]
        # 群聊里要能看出这条是从 QQ 来的（界面画"来源"标识就靠它）
        assert via == "qq", rows
        assert sender_type == "human"
        assert content == "@浮生 今天天气不错", content      # 两边都要求"点名"，前缀是桥接的一部分
        assert username == "小明"                            # 说话人是谁，AI 得看得出来

        # 重复推送同一条（官方明说可能重复）→ 不能回答两遍
        await plugin._on_group_at(dict(GROUP_EVENT))
        assert len(await _messages(GROUP_ID)) == 1

        # 白名单：不在名单里的群直接丢弃
        plugin._allow = {"QQGROUP-OTHER"}
        await plugin._on_group_at({**GROUP_EVENT, "id": "MSG-2"})
        assert len(await _messages(GROUP_ID)) == 1
        plugin._allow = set()

        # 出站：AI 的回复要回到发消息的那个 QQ 群（被动回复带 msg_id）
        async with async_session() as db:
            await send_gm_message(db, group_id=GROUP_ID, sender_type="ai",
                                  sender_id=AGENT_USER, content="是挺好的")
            await db.commit()
        await _wait_sent(plugin)
        assert plugin._client.sent, "AI 回复没有发回 QQ"
        sent = plugin._client.sent[0]
        assert sent["kind"] == "group" and sent["target"] == "QQGROUP-AAA"
        assert sent["content"] == "是挺好的"
        assert sent["msg_id"] == "MSG-1" and sent["seq"] == 1   # 被动回复 5 分钟内最多 5 次

        # 同一个 msg_id 再回一条：seq 必须往前推，否则官方判定重复发送直接失败
        async with async_session() as db:
            await send_gm_message(db, group_id=GROUP_ID, sender_type="ai",
                                  sender_id=AGENT_USER, content="确实")
            await db.commit()
        for _ in range(50):
            if len(plugin._client.sent) > 1:
                break
            await asyncio.sleep(0.01)
        assert plugin._client.sent[1]["seq"] == 2, plugin._client.sent
    finally:
        _cleanup(plugin)


async def test_private_chat_round_trip(migrated_db):
    """私聊：QQ 用户 → 与该 AI 的私信会话 → AI 回复回到这个 QQ 用户"""
    from app.chat.dm import get_or_create_dm_session, send_dm_message
    from app.database import async_session

    await _seed()
    plugin = await _make_plugin()
    plugin._dm_policy = "open"          # 这条用例验的是投递链路本身，配对门槛另有用例
    try:
        await plugin._on_c2c(dict(DM_EVENT))

        async with async_session() as db:
            row = (await db.execute(text(
                "SELECT dm.session_id, m.content, u.username FROM dm_messages m "
                "JOIN dm_sessions dm ON dm.session_id = m.session_id "
                "JOIN users u ON u.id = m.sender_id"
            ))).first()
        assert row is not None, "私聊消息没有落库"
        session_id, content, username = row
        assert content == "在吗" and username == "小红"

        # AI 回复 → 通过私信出口回 QQ（被动回复用触发它的那条私信 id）
        async with async_session() as db:
            await send_dm_message(db, session_id, sender_id=AGENT_USER, content="在的")
            await db.commit()
        await _wait_sent(plugin)
        assert plugin._client.sent, "AI 的私信回复没有发回 QQ"
        sent = plugin._client.sent[0]
        assert sent["kind"] == "dm" and sent["target"] == "OPENID-DM"
        assert sent["content"] == "在的" and sent["msg_id"] == "DMMSG-1"
    finally:
        _cleanup(plugin)


async def test_dm_pairing_gate_then_approved(migrated_db):
    """默认 pairing：陌生人私聊不进 AI，只领一个配对码；主人批准之后才放行"""
    from app.database import async_session
    from app.services.plugin import pairing

    await _seed()
    plugin = await _make_plugin()
    try:
        await plugin._on_c2c(dict(DM_EVENT))

        async with async_session() as db:
            count = (await db.execute(text("SELECT count(*) FROM dm_messages"))).scalar()
        assert count == 0, "没配对的人不该进入 AI 的私信"
        assert plugin._client.sent, "应该回一个配对码"
        assert plugin._client.sent[0]["kind"] == "dm" and "配对码" in plugin._client.sent[0]["content"]

        async with async_session() as db:
            row = (await db.execute(text(
                "SELECT kind, origin, display_name, code, status FROM external_identities"
            ))).first()
        assert row is not None and row[0] == "qq" and row[4] == "pending" and row[2] == "小红", row

        # 主人批准 → 再私聊就进 AI
        async with async_session() as db:
            await pairing.approve(db, kind="qq", owner_scope="bot-a", code=row[3])
        plugin._client.sent.clear()
        await plugin._on_c2c({**DM_EVENT, "id": "DMMSG-2"})
        async with async_session() as db:
            count = (await db.execute(text("SELECT count(*) FROM dm_messages"))).scalar()
        assert count == 1, "批准之后私聊应该进 AI"

        # off：一律不处理
        plugin._dm_policy = "off"
        await plugin._on_c2c({**DM_EVENT, "id": "DMMSG-3"})

        # owner + 陌生人：静默忽略（不入库，也不回码，免得替主人拉客）
        plugin._dm_policy = "owner"
        plugin._client.sent.clear()
        await plugin._on_c2c({**DM_EVENT, "id": "DMMSG-4",
                              "author": {"user_openid": "OPENID-STRANGER", "username": "路人"}})
        async with async_session() as db:
            count = (await db.execute(text("SELECT count(*) FROM dm_messages"))).scalar()
        assert count == 1, "off/owner 下不该新增私信"
        assert plugin._client.sent == [], "owner 策略下不应给陌生人回配对码"
    finally:
        _cleanup(plugin)


async def test_multi_instance_one_per_config(migrated_db):
    """一个插件多份配置：每个实例带自己的凭据，删掉配置的实例会被回收"""
    from app.database import async_session
    from app.models.plugin import Plugin
    from app.services.infrastructure.plugin_registry import PluginRegistry
    from app.services.plugin import skill_bridge
    from app.services.plugin.config import set_config

    await _seed()
    try:
        async with async_session() as db:
            db.add(Plugin(id="qq-channel", name="QQ 通道", category="service", enabled=True, builtin=True))
            await db.commit()
            await set_config("qq-channel", {"app_id": "111", "client_secret": "s1", "target_agent": "浮生"},
                             "bot-a", db=db)
            await set_config("qq-channel", {"app_id": "222", "client_secret": "s2", "target_agent": "浮生"},
                             "bot-b", db=db)
            await skill_bridge.apply_skill_plugins(db)

            a = PluginRegistry.get("qq-channel:bot-a")
            b = PluginRegistry.get("qq-channel:bot-b")
            assert a is not None and b is not None, "多实例没有被逐个建起来"
            assert a.instance == "bot-a" and b.instance == "bot-b"
            assert a.multi_instance is True and b.multi_instance is True
            assert (await a.config())["app_id"] == "111"      # 各自的配置不能串
            assert (await b.config())["app_id"] == "222"
            assert (await a.config())["client_secret"] == "s1"
            assert PluginRegistry.keys_of("qq-channel") == ["qq-channel:bot-a", "qq-channel:bot-b"]

            # 删掉一份配置 → 运行中的实例跟着少一个
            await db.execute(text(
                "DELETE FROM plugin_configs WHERE plugin_id='qq-channel' AND instance='bot-b'"
            ))
            await db.commit()
            await skill_bridge.apply_skill_plugins(db)
            assert PluginRegistry.get("qq-channel:bot-b") is None
            assert PluginRegistry.get("qq-channel:bot-a") is not None
    finally:
        async with async_session() as db:
            await skill_bridge._unload_plugin("qq-channel")
            await db.execute(text("TRUNCATE plugin_service_states, plugin_configs, plugins CASCADE"))
            await db.commit()


async def test_non_ai_or_other_group_is_not_forwarded(migrated_db):
    """出口只转发"绑定群里这个 AI 说的话"：人说的、别的群说的都不该外流"""
    from app.chat.gm import send_gm_message
    from app.database import async_session

    await _seed()
    plugin = await _make_plugin()
    try:
        plugin._route[GROUP_ID] = {"qq": "QQGROUP-AAA", "msg_id": "MSG-1", "seq": 0, "ts": 0}
        async with async_session() as db:
            await send_gm_message(db, group_id=GROUP_ID, sender_type="human",
                                  sender_id=GROUP_OWNER, content="人类发言")
            await db.commit()
        await asyncio.sleep(0.05)
        assert plugin._client.sent == [], "人类发言不该转发到 QQ"
    finally:
        _cleanup(plugin)


async def test_broken_sink_does_not_break_message_sending(migrated_db):
    """外部通道坏了不能拖垮"发消息"本身：单个 sink 抛异常只记日志"""
    from app.chat.gm import send_gm_message
    from app.chat.outbound import unregister_sink, register_sink
    from app.database import async_session

    await _seed()
    seen = []

    async def good(db, group_id, message, source):
        seen.append((group_id, message.content))

    async def boom(db, group_id, message, source):
        raise RuntimeError("通道炸了")

    register_sink("test-good", group=good)
    register_sink("test-boom", group=boom)
    try:
        async with async_session() as db:
            await send_gm_message(db, group_id=GROUP_ID, sender_type="human",
                                  sender_id=GROUP_OWNER, content="照常发送")
            await db.commit()
        assert seen == [(GROUP_ID, "照常发送")], seen
    finally:
        unregister_sink("test-good")
        unregister_sink("test-boom")


async def test_group_nickname_backfills_username(migrated_db):
    """腾讯的群事件带 username；第一次没拿到时先用占位名，之后要补上。

    不补的话，Coprope 界面、AI 眼里的说话人名、AI 回 @ 时用的名字，永远都是「QQ用户XXXXXX」。
    """
    from app.database import async_session

    await _seed()
    plugin = await _make_plugin()
    plugin._copree_group_id = GROUP_ID
    try:
        # 第一条没有昵称 → 占位名建号
        await plugin._on_group_at({**GROUP_EVENT, "id": "MSG-N1", "author": {"member_openid": "OPENID-N"}})
        async with async_session() as db:
            name = (await db.execute(text(
                "SELECT username FROM users WHERE email = 'OPENID-N@qq.bridge'"
            ))).scalar()
        assert name == "QQ用户OPENID", name

        # 第二条带昵称 → 补上（站内显示名 + 外部身份展示名一起）
        await plugin._on_group_at({**GROUP_EVENT, "id": "MSG-N2",
                                   "author": {"member_openid": "OPENID-N", "username": "书爱 Shu Ai"}})
        async with async_session() as db:
            name = (await db.execute(text(
                "SELECT username FROM users WHERE email = 'OPENID-N@qq.bridge'"
            ))).scalar()
            display = (await db.execute(text(
                "SELECT display_name FROM external_identities WHERE origin = 'OPENID-N'"
            ))).scalar()
        assert name == "书爱 Shu Ai", name
        assert display == "书爱 Shu Ai", display
    finally:
        _cleanup(plugin)


async def test_group_outbound_strips_reply_mention(migrated_db):
    """转发给 QQ 的回复要去掉开头那个 @：QQ 的被动回复自己就会显示「@对方」。

    用户 2026-09-25 实测：QQ 侧显示「@书爱… @QQ用户6682BD 正文」两个 @。
    站内（Copree）不动 —— 界面要靠它显示 AI 在回复谁。
    """
    from app.database import async_session

    await _seed()
    plugin = await _make_plugin()
    plugin._copree_group_id = GROUP_ID
    plugin._route[GROUP_ID] = {"qq": "QQGROUP-AAA", "msg_id": "MSG-1", "peer_name": "小明"}

    class _FakeMsg:
        sender_type = "ai"
        content = "@小明 今天天气不错，出去走走"

    async with async_session() as db:
        await plugin._outbound_sink(db, GROUP_ID, _FakeMsg(), "ai")
    await _wait_sent(plugin)
    _cleanup(plugin)

    assert plugin._client.sent, "AI 的回复应该发回 QQ"
    assert plugin._client.sent[0]["content"] == "今天天气不错，出去走走", plugin._client.sent[0]


async def test_group_message_broadcasts_to_humans(migrated_db):
    """外部通道来的群消息必须像网页端那样实时广播给群里的人。

    用户 2026-09-25 实测：QQ 群里说了话，Copree 侧界面上不出现，**刷新之后才有** ——
    插件那条链路少调了 manager.broadcast_to_group（网页端发消息那条链路是有的），
    而 fanout_group_message 只管 AI 成员与离线暂存，不推人。
    """
    from app.routers.ws import manager

    await _seed()
    plugin = await _make_plugin()
    plugin._copree_group_id = GROUP_ID

    calls: list[tuple[int, dict]] = []
    original = manager.broadcast_to_group

    async def _spy(group_id, message, exclude_user_id=None):
        calls.append((group_id, message))

    manager.broadcast_to_group = _spy
    try:
        await plugin._on_group_at(dict(GROUP_EVENT))
    finally:
        manager.broadcast_to_group = original
        _cleanup(plugin)

    assert calls, "群里的人应该实时收到这条 QQ 消息（否则只能刷新才看到）"
    group_id, payload = calls[0]
    assert group_id == GROUP_ID
    assert payload["data"]["via"] == "qq"
    assert payload["data"]["content"].startswith("@浮生")
    assert payload["data"]["sender_name"] == "小明"


def test_readable_content_covers_media_placeholders():
    module = _load_plugin_module()
    plugin = module.QqChannelPlugin()
    assert plugin._readable_content({"content": " 你好 "}) == "你好"
    assert plugin._readable_content({"attachments": [{"content_type": "image/png"}]}) == "[图片]"
    assert plugin._readable_content({"attachments": [{"content_type": "voice", "asr_refer_text": "晚安"}]}) == "[语音] 晚安"
    assert plugin._readable_content({"attachments": [{"content_type": "file", "filename": "报表.xlsx"}]}) == "[文件] 报表.xlsx"
