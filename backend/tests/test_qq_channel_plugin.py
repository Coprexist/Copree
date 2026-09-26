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

    async def send_group(self, group_openid, content, msg_id=None, msg_seq=1, message_reference="",
                         force_type=None):
        self.sent.append({"kind": "group", "target": group_openid, "content": content,
                          "msg_id": msg_id, "seq": msg_seq, "reference": message_reference,
                          "force_type": force_type})
        return {"id": "fake", "ext_info": {"ref_idx": "REFIDX-OUT"}}

    async def send_c2c(self, user_openid, content, msg_id=None, msg_seq=1):
        self.sent.append({"kind": "dm", "target": user_openid, "content": content,
                          "msg_id": msg_id, "seq": msg_seq})
        return {"id": "fake"}


async def _seed():
    from app.database import async_session

    async with async_session() as db:
        from db_reset import clear
        await clear(db, "external_identities", "plugin_service_states", "plugin_configs", "plugins",
                    "pending_messages", "messages", "dm_messages", "dm_sessions", "group_members",
                    "groups", "users", "agents")
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
    register_sink(plugin.key, group=plugin._outbound_sink, dm=plugin._dm_outbound_sink)

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


async def _ledger(agent_id: int) -> list[dict]:
    """这个 AI 在测试群里的账本条目（seq 升序）"""
    from app.database import async_session
    from app.services.history import history_service as hs
    from app.services.history.context_sync import context_ref

    async with async_session() as db:
        return await hs.read(db, agent_id, context_ref(group_id=GROUP_ID))


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
        # （条目里的键叫 origin：通道侧标识在身份层统一是这个名，插件侧不再用 openid 当字段名）
        status = await plugin.get_status()
        assert [g["origin"] for g in status["recent_groups"]] == ["QQGROUP-AAA"], status["recent_groups"]
        assert status["recent_groups"][0]["count"] == 1 and status["recent_groups"][0]["allowed"] is True
        assert status["recent_field"] == "qq_group_allowlist"

        # 白名单把群挡下时也要记账（allowed=False），否则用户永远发现不了这个群
        plugin._allow = {"SOME-OTHER-GROUP"}
        await plugin._on_group_at({**GROUP_EVENT, "id": "MSG-2", "group_openid": "QQGROUP-BBB"})
        status = await plugin.get_status()
        blocked = {g["origin"]: g for g in status["recent_groups"]}["QQGROUP-BBB"]
        assert blocked["allowed"] is False, blocked
        assert len(await _messages(GROUP_ID)) == 1, "被白名单挡下的群不该进 Copree"

        rows = await _messages(GROUP_ID)
        assert len(rows) == 1, rows
        sender_type, _sender_id, content, username, via = rows[0]
        # 群聊里要能看出这条是从 QQ 来的（界面画"来源"标识就靠它）
        assert via == "qq", rows
        assert sender_type == "human"
        # 两边都要求"点名"，前缀是桥接的一部分；入口把点名的写法归一成 id 令牌
        assert content == "<@!2> 今天天气不错", content
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
        code_msg = plugin._client.sent[0]["content"]
        assert plugin._client.sent[0]["kind"] == "dm" and "配对码" in code_msg
        # 文案是两条通道共用的一份（用户 2026-09-26 定）：本 AI + 创建者提示 + 开源地址
        assert "此 AI" in code_msg and "如果你不是我的创建者" in code_msg
        assert "github.com/Coprexist/Copree" in code_msg

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
            from db_reset import clear
            await clear(db, "plugin_service_states", "plugin_configs", "plugins")
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


async def test_self_test_sends_on_the_live_route(migrated_db):
    """通道自测：在最近收到消息的那条路由上真发一条，把腾讯的原样回答带回来

    它要回答的是一个具体问题：群消息的正文认不认 <@!openid> 这种内联 @。
    所以探针必须两种写法并排；而且必须只走被动回复——窗口过期时宁可失败，
    也不能拿一条有配额的主动消息冒充"通"。
    """
    import time

    await _seed()
    plugin = await _make_plugin()
    try:
        result = await plugin.self_test()
        assert result["sent"] is False and "还没有收到过消息" in result["reason"], result

        plugin._route[GROUP_ID] = {
            "qq": "QQGROUP-AAA", "msg_id": "MSG-1", "seq": 0, "ts": time.time(),
            "peer_name": "小明", "peer_openid": "OPENID-XYZ",
        }
        result = await plugin.self_test()
        assert result["sent"] is True and result["mode"] == "passive", result
        assert result["response"]["id"] == "fake", result
        sent = plugin._client.sent[-1]
        assert sent["kind"] == "group" and sent["msg_id"] == "MSG-1" and sent["seq"] == 1, sent
        assert "<@!OPENID-XYZ>" in sent["content"] and "@小明" in sent["content"], sent

        plugin._route[GROUP_ID]["ts"] = 0                      # 窗口早就过期
        result = await plugin.self_test()
        assert result["sent"] is False and "被动回复窗口已过" in result["reason"], result
        assert len(plugin._client.sent) == 1, "窗口过期时不该偷偷发一条主动消息"
    finally:
        _cleanup(plugin)


async def test_group_outbound_strips_reply_mention(migrated_db):
    """回复「@ 事件」送来的消息时，去掉开头那个 @：腾讯自己会补「@对方」。

    用户 2026-09-25 实测：QQ 侧显示「@书爱… @QQ用户6682BD 正文」两个 @。
    站内（Copree）不动 —— 界面要靠它显示 AI 在回复谁。
    全量事件送进来的消息不走这条路（腾讯不补 @，摘了就没了），见下一条用例。
    """
    from app.database import async_session

    await _seed()
    plugin = await _make_plugin()
    plugin._copree_group_id = GROUP_ID
    plugin._route[GROUP_ID] = {"qq": "QQGROUP-AAA", "msg_id": "MSG-1", "peer_name": "小明",
                               "at_event": True}

    class _FakeMsg:
        sender_type = "ai"
        content = "@小明 今天天气不错，出去走走"

    async with async_session() as db:
        await plugin._outbound_sink(db, GROUP_ID, _FakeMsg(), "ai")
    await _wait_sent(plugin)
    _cleanup(plugin)

    assert plugin._client.sent, "AI 的回复应该发回 QQ"
    assert plugin._client.sent[0]["content"] == "今天天气不错，出去走走", plugin._client.sent[0]


async def test_plain_text_reply_falls_back_to_the_name(migrated_db):
    """纯文本模式发不出真 @：腾讯把内联 @ 原样显示成尖括号（2026-09-26 真机自测），

    所以这条通道按纯文本发时退成 @名字——不提醒对方，但人看得懂。
    走全量事件这条路（不摘开头 @）：@ 事件的回复腾讯会自己补 @，摘不摘与纯文本无关。
    """
    from app.database import async_session

    await _seed()
    plugin = await _make_plugin()
    plugin._copree_group_id = GROUP_ID
    plugin._msg_type = 0                      # 配置里选了「纯文本」
    try:
        await plugin._on_group_message({**GROUP_EVENT, "id": "PLAIN-1", "content": "你好",
                                        "mentions": []})
        async with async_session() as db:
            uid = (await db.execute(text(
                "SELECT id FROM users WHERE email = 'OPENID-XYZ@qq.bridge'"
            ))).scalar()

        class _FakeMsg:
            sender_type = "ai"
            content = f"<@!{uid}> 你好"

        async with async_session() as db:
            await plugin._outbound_sink(db, GROUP_ID, _FakeMsg(), "ai")
        await _wait_sent(plugin)

        sent = plugin._client.sent[-1]
        assert sent["force_type"] == 0, sent
        assert sent["content"] == "@小明 你好", sent      # 名字，不是尖括号原文
        assert "OPENID" not in sent["content"], sent
    finally:
        _cleanup(plugin)


async def test_full_mode_reply_sends_the_mention_itself(migrated_db):
    """全量模式进来的消息被回复时，@ 得我们自己发：腾讯只对「@ 事件」的回复补 @对方。

    2026-09-26 真机：全量事件送来的消息（哪怕正文 @ 了机器人）被回复，开头那个 @ 摘掉后
    QQ 侧一个 @ 都没有；同一天的 @ 事件回复腾讯会自己补。所以摘不摘按**事件类型**分。
    """
    from app.database import async_session

    await _seed()
    plugin = await _make_plugin()
    plugin._copree_group_id = GROUP_ID
    try:
        # 全量事件（没点名）→ 路由记 addressed=False
        await plugin._on_group_message({**GROUP_EVENT, "id": "FULL-1", "content": "今天天气不错",
                                        "mentions": []})
        async with async_session() as db:
            uid = (await db.execute(text(
                "SELECT id FROM users WHERE email = 'OPENID-XYZ@qq.bridge'"
            ))).scalar()
        assert uid, "入站应该建出锚点账号"
        assert plugin._route[GROUP_ID].get("at_event") is False, plugin._route[GROUP_ID]

        class _FakeMsg:
            sender_type = "ai"
            content = f"<@!{uid}> 今天天气不错"

        async with async_session() as db:
            await plugin._outbound_sink(db, GROUP_ID, _FakeMsg(), "ai")
        await _wait_sent(plugin)

        sent = plugin._client.sent[-1]["content"]
        assert sent == "<@!OPENID-XYZ> 今天天气不错", sent   # 没摘，翻成真 @ 发出去
    finally:
        _cleanup(plugin)


async def test_outbound_mentions_become_real_qq_at(migrated_db):
    """出站把 <@!平台id> 翻成 QQ 的真 @：走过通道的人才有 openid 可 @，其余退名字/丢掉。

    真机实测（2026-09-25）：正文里的 <@!openid> 在群里渲染成**可点**的 @对方。
    开头对**这次回的那个人**的 @ 仍然先摘掉——QQ 的被动回复自己就显示 @对方，两个 @ 很蠢。
    """
    from app.database import async_session

    await _seed()
    plugin = await _make_plugin()
    plugin._copree_group_id = GROUP_ID
    try:
        await plugin._on_group_at({**GROUP_EVENT, "id": "MSG-M1"})      # 让这个人从通道进来
        async with async_session() as db:
            uid = (await db.execute(text(
                "SELECT id FROM users WHERE email = 'OPENID-XYZ@qq.bridge'"
            ))).scalar()
        assert uid, "入站应该建出锚点账号"

        class _FakeMsg:
            sender_type = "ai"
            content = f"好的 <@!{uid}> 你看这条，还有 <@!999999>"

        plugin._client.sent.clear()
        async with async_session() as db:
            await plugin._outbound_sink(db, GROUP_ID, _FakeMsg(), "ai")
        await _wait_sent(plugin)

        sent = plugin._client.sent[-1]["content"]
        assert "<@!OPENID-XYZ>" in sent, sent                     # 真 @（不是名字文本）
        assert "999999" not in sent, sent                         # 认不出的令牌不能原样发出去

        # 回给「刚说话的那个人」时，开头的 @ 摘掉（否则 QQ 侧两个 @）
        class _FakeReply:
            sender_type = "ai"
            content = f"<@!{uid}> 你好"

        plugin._client.sent.clear()
        async with async_session() as db:
            await plugin._outbound_sink(db, GROUP_ID, _FakeReply(), "ai")
        await _wait_sent(plugin)
        assert plugin._client.sent[-1]["content"] == "你好", plugin._client.sent[-1]
    finally:
        _cleanup(plugin)


async def test_full_mode_mirrors_everything_but_only_wakes_when_addressed(migrated_db):
    """全量模式：有消息就进 Copree（能拿到多少拿多少），但只有点名到机器人才叫 AI。

    没开这个功能的号根本收不到 GROUP_MESSAGE_CREATE，@ 那条路照旧——两条都要能用。
    """
    await _seed()
    plugin = await _make_plugin()
    plugin._copree_group_id = GROUP_ID
    try:
        await plugin._on_group_message({**GROUP_EVENT, "id": "FULL-1", "content": "今天天气不错", "mentions": []})
        rows = await _messages(GROUP_ID)
        assert len(rows) == 1 and rows[0][2] == "今天天气不错", rows    # 入库，但没有唤醒令牌

        await plugin._on_group_message({**GROUP_EVENT, "id": "FULL-2", "content": "你看这个",
                                        "mentions": [{"id": "BOT-OPENID", "bot": True}]})
        rows = await _messages(GROUP_ID)
        assert len(rows) == 2 and rows[1][2].startswith("<@!2>"), rows  # 点名 → 带唤醒令牌
    finally:
        _cleanup(plugin)


async def test_quote_replies_can_be_turned_off(migrated_db):
    """「引用回复」是独立维度：关掉就不带 message_reference，正文格式照旧不受影响。"""
    import time

    from app.chat.gm import send_gm_message
    from app.database import async_session

    await _seed()
    plugin = await _make_plugin()
    plugin._quote_replies = False
    plugin._copree_group_id = GROUP_ID
    plugin._route[GROUP_ID] = {"qq": "QQGROUP-AAA", "msg_id": "MSG-1", "seq": 0, "ts": time.time()}
    try:
        async with async_session() as db:
            inbound = await send_gm_message(db, group_id=GROUP_ID, sender_type="human", sender_id=1,
                                            content="问题")
            inbound.channel_ref_idx = "REFIDX-IN"
            await db.commit()
            inbound_id = inbound.id

        plugin._client.sent.clear()
        async with async_session() as db:
            await send_gm_message(db, group_id=GROUP_ID, sender_type="ai", sender_id=AGENT_USER,
                                  content="回答", reply_to=inbound_id)
            await db.commit()
        await _wait_sent(plugin)
        assert plugin._client.sent[-1]["reference"] == "", plugin._client.sent[-1]
    finally:
        _cleanup(plugin)


async def test_configured_message_type_is_used(migrated_db):
    """卡片里固定了消息类型（比如 1）就按它发，不再"先 Markdown 后降级"。

    有的 QQ 客户端只显示特定类型，所以类型要能选（用户 2026-09-26 提）。
    """
    import time

    from app.database import async_session
    from app.models.plugin import PluginConfig  # noqa: F401  （只是提醒：值来自插件配置）

    await _seed()
    plugin = await _make_plugin()
    plugin._msg_type = 0                      # 即配置里选了「纯文本」
    plugin._copree_group_id = GROUP_ID
    plugin._route[GROUP_ID] = {"qq": "QQGROUP-AAA", "msg_id": "MSG-1", "seq": 0, "ts": time.time()}
    try:
        class _FakeMsg:
            sender_type = "ai"
            content = "纯文本的测试"

        async with async_session() as db:
            await plugin._outbound_sink(db, GROUP_ID, _FakeMsg(), "ai")
        await _wait_sent(plugin)
        assert plugin._client.sent[-1]["force_type"] == 0, plugin._client.sent[-1]
    finally:
        _cleanup(plugin)


def test_body_format_config_parsing():
    """配置里写意图（plain/markdown），不是协议数字：官方发送侧只有 0/2/3/7，1 根本不存在。"""
    module = _load_plugin_module()

    assert module._msg_type_of("") is None            # 默认：先 Markdown，没权限降级纯文本
    assert module._msg_type_of(None) is None
    assert module._msg_type_of("abc") is None
    assert module._msg_type_of("1") is None           # 不存在的取值
    assert module._msg_type_of("plain") == 0
    assert module._msg_type_of("markdown") == 2
    assert module._msg_type_of(" 2 ") == 2            # 老配置/手填的数字照样认


async def test_outbound_reply_quotes_the_qq_message(migrated_db):
    """AI 回复 reply_to 指向一条 QQ 来消息 → 发 QQ 时带 message_reference（精准引用那条）。

    入站事件的 msg_idx 就是那条消息的 REFIDX；出站响应里的 ext_info.ref_idx 也记下来，
    这样"引用机器人自己说过的话"以后也有得用。
    """
    import asyncio

    from sqlalchemy import text

    from app.chat.gm import send_gm_message
    from app.database import async_session

    await _seed()
    plugin = await _make_plugin()
    plugin._copree_group_id = GROUP_ID
    try:
        await plugin._on_group_at({
            **GROUP_EVENT, "id": "Q-1", "content": "问题",
            "message_scene": {"source": "default", "ext": ["msg_idx=REFIDX-IN"]},
        })
        async with async_session() as db:
            inbound = (await db.execute(text(
                "SELECT id, channel_ref_idx FROM messages WHERE group_id = :g ORDER BY id DESC LIMIT 1"
            ), {"g": GROUP_ID})).first()
        assert inbound is not None and inbound[1] == "REFIDX-IN", inbound

        plugin._client.sent.clear()
        async with async_session() as db:
            await send_gm_message(db, group_id=GROUP_ID, sender_type="ai", sender_id=AGENT_USER,
                                  content="回答", reply_to=int(inbound[0]))
            await db.commit()
        await _wait_sent(plugin)
        assert plugin._client.sent[-1]["reference"] == "REFIDX-IN", plugin._client.sent[-1]

        # 出站那条也把通道给的 ref_idx 记下来（另开 session 回写，等它落库）
        outbound = None
        for _ in range(40):
            async with async_session() as db:
                outbound = (await db.execute(text(
                    "SELECT channel_msg_id, channel_ref_idx FROM messages "
                    "WHERE group_id = :g ORDER BY id DESC LIMIT 1"
                ), {"g": GROUP_ID})).first()
            if outbound == ("fake", "REFIDX-OUT"):
                break
            await asyncio.sleep(0.05)
        assert outbound == ("fake", "REFIDX-OUT"), outbound
    finally:
        _cleanup(plugin)


async def test_full_mode_materializes_mentioned_members(migrated_db):
    """全量事件里 @ 到的人：先建成 Copree 群成员，正文缺提及就补 <@!id>。

    被 @ 的人可能还没说过话（没账号）；QQ 也可能把 @成员的提及从正文摘掉——两条都在这补。
    """
    from sqlalchemy import text

    from app.database import async_session

    await _seed()
    plugin = await _make_plugin()
    plugin._copree_group_id = GROUP_ID
    try:
        await plugin._on_group_message({
            **GROUP_EVENT, "id": "MENTION-1", "content": "你看这个",
            "mentions": [{"id": "BOT-OPENID", "bot": True},
                         {"id": "OPENID-NEW", "username": "小红"}],
        })
        rows = await _messages(GROUP_ID)
        assert len(rows) == 1, rows
        async with async_session() as db:
            uid = (await db.execute(text(
                "SELECT id FROM users WHERE email = 'OPENID-NEW@qq.bridge'"
            ))).scalar()
            member = (await db.execute(text(
                "SELECT count(*) FROM group_members WHERE group_id = :g AND member_id = :m"
            ), {"g": GROUP_ID, "m": uid})).scalar()
        assert uid, "被 @ 的人应该建出锚点账号"
        assert member == 1, "被 @ 的人应该进这个 Copree 群"
        assert f"<@!{uid}>" in rows[0][2], rows
    finally:
        _cleanup(plugin)


async def test_push_mode_flip_reaches_the_ledger(migrated_db):
    """推送模式翻转 → 给 AI 的账本投一条通知；同一种模式只投一次（重启后再观测到也不重复）。

    判据是事件类型：GROUP_MESSAGE_CREATE = 腾讯在喂全量（2026-09-26 真机：开了之后连 @ 的
    消息也只来这一条）。幂等以账本为准，所以"重启后不知道旧模式"也不会重复投。
    """
    await _seed()
    plugin = await _make_plugin()
    plugin._copree_group_id = GROUP_ID
    try:
        assert plugin.observed_full_mode() is None, "本次启动后还没收到群消息 → 不知道"

        # 老规矩（只喂点名）不用通知：它本来就是这么以为的
        await plugin._on_group_at({**GROUP_EVENT, "id": "MODE-AT-1"})
        assert plugin.observed_full_mode() is False
        assert await _ledger(7) == []

        # 开了全量 → 投一条
        await plugin._on_group_message({**GROUP_EVENT, "id": "MODE-FULL-1", "content": "今天天气不错",
                                        "mentions": []})
        assert plugin.observed_full_mode() is True
        entries = await _ledger(7)
        assert len(entries) == 1 and entries[0]["flags"]["channel_mode"] == "full", entries
        assert "全量模式" in entries[0]["content"], entries[0]

        # 同一种模式：再观测到不重复；"重启"（全新实例，内存里没有旧模式）也不重复
        await plugin._on_group_message({**GROUP_EVENT, "id": "MODE-FULL-2", "content": "又一条",
                                        "mentions": []})
        restarted = await _make_plugin()
        restarted._copree_group_id = GROUP_ID
        try:
            await restarted._on_group_message({**GROUP_EVENT, "id": "MODE-FULL-3", "content": "重启后",
                                               "mentions": []})
        finally:
            _cleanup(restarted)
        assert len(await _ledger(7)) == 1, "同一种模式只投一次"

        # 关掉全量：@ 事件回来 → 再投一条"停止"
        await plugin._on_group_at({**GROUP_EVENT, "id": "MODE-AT-2"})
        entries = await _ledger(7)
        assert len(entries) == 2 and entries[1]["flags"]["channel_mode"] == "at", entries
        assert "停止" in entries[1]["content"], entries[1]
    finally:
        _cleanup(plugin)


async def test_full_mode_raw_mentions_do_not_reach_the_ai(migrated_db):
    """QQ 正文里的原始提及 `<@openid>`（没有那个 `!`）谁都不认识，一律摘掉。

    2026-09-26 真机：@机器人 那条被存成 `"<@!40> <@5872…> 在？"`——AI 看到一串 openid，
    回了一句「你 @ 的是谁，我这边看不到」，界面也跟着显示乱码。
    机器人自己那条只摘不补（点名由唤醒令牌负责），别的成员摘掉后补平台的 <@!id>。
    """
    from sqlalchemy import text

    from app.database import async_session

    await _seed()
    plugin = await _make_plugin()
    plugin._copree_group_id = GROUP_ID
    try:
        await plugin._on_group_message({
            **GROUP_EVENT, "id": "RAW-1",
            "content": "<@BOT-OPENID> 你看这个 <@OPENID-NEW>",
            "mentions": [{"id": "BOT-OPENID", "bot": True},
                         {"id": "OPENID-NEW", "username": "小红"}],
        })
        rows = await _messages(GROUP_ID)
        async with async_session() as db:
            uid = (await db.execute(text(
                "SELECT id FROM users WHERE email = 'OPENID-NEW@qq.bridge'"
            ))).scalar()
        stored = rows[0][2]
        assert "BOT-OPENID" not in stored, stored          # 机器人自己那条不留下
        assert "OPENID-NEW" not in stored, stored          # 生 openid 不留下
        assert f"<@!{uid}>" in stored, stored              # 换成平台令牌
        assert "你看这个" in stored, stored
    finally:
        _cleanup(plugin)


async def test_full_mode_event_completes_the_truncated_text(migrated_db):
    """同一条消息的两个事件：@模式先到（正文在"@其他成员"处断了），全量事件后到就把正文补上。"""
    await _seed()
    plugin = await _make_plugin()
    plugin._copree_group_id = GROUP_ID
    try:
        truncated = "今天天气"
        full = "今天天气不错 @小明 你说是吧"
        await plugin._on_group_at({**GROUP_EVENT, "id": "DUP-1", "content": truncated})
        rows = await _messages(GROUP_ID)
        assert len(rows) == 1 and truncated in rows[0][2], rows

        await plugin._on_group_message({**GROUP_EVENT, "id": "DUP-1", "content": full,
                                        "mentions": [{"id": "BOT-OPENID", "bot": True}]})
        rows = await _messages(GROUP_ID)
        assert len(rows) == 1, rows                      # 不新增一条
        assert full in rows[0][2], rows                  # 正文补全了
    finally:
        _cleanup(plugin)


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
    assert payload["data"]["content"].startswith("<@!2>")
    assert payload["data"]["sender_name"] == "小明"


def test_readable_content_covers_media_placeholders():
    module = _load_plugin_module()
    plugin = module.QqChannelPlugin()
    assert plugin._readable_content({"content": " 你好 "}) == "你好"
    assert plugin._readable_content({"attachments": [{"content_type": "image/png"}]}) == "[图片]"
    assert plugin._readable_content({"attachments": [{"content_type": "voice", "asr_refer_text": "晚安"}]}) == "[语音] 晚安"
    assert plugin._readable_content({"attachments": [{"content_type": "file", "filename": "报表.xlsx"}]}) == "[文件] 报表.xlsx"

async def test_markdown_first_with_plain_fallback():
    """机器人有 MD 权限就发 Markdown，没有就在同一次发送里退回纯文本

    MD 权限是机器人账号维度的（腾讯开通），所以降级放在发送这一层，不写死在平台配置里。
    """
    module = _load_plugin_module()
    client = module.QqClient("app-id", "secret")
    sent: list[dict] = []

    async def fake_post(path, body):
        sent.append(body)
        if len(sent) == 1:
            raise RuntimeError("发 QQ 消息失败（HTTP 400）：{'message': '无权限'}")
        return {"id": "msg-1"}

    client._post = fake_post
    await client.send_group("GROUP-OPENID", "**粗体** 与 `代码`", msg_id="M-1", msg_seq=1)

    assert sent[0]["msg_type"] == 2, sent
    assert sent[0]["markdown"]["content"].startswith("**粗体**"), sent
    assert sent[0]["msg_id"] == "M-1" and sent[0]["msg_seq"] == 1, sent
    assert sent[1]["msg_type"] == 0, sent
    assert "**" not in sent[1]["content"] and "`代码`" not in sent[1]["content"], sent

