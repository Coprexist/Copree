"""QQ 通道（NapCat / OneBot v11）插件：入站进 Copree、出站回 QQ、@ 唤醒、私聊配对

不起真实 WebSocket、不连外网 —— 只喂 OneBot v11 事件、看消息怎么走
（那是这个插件的全部逻辑所在），以及出站有没有把 AI 写的 Markdown 原样发出去。
"""
import asyncio
import importlib.util
import json
import os
from pathlib import Path

from sqlalchemy import text

PLUGIN_PATH = Path(__file__).resolve().parents[1] / "plugins" / "qq-napcat" / "plugin.py"
MANIFEST_PATH = PLUGIN_PATH.parent / "plugin.json"
GROUP_ID = 5
GROUP_OWNER, AGENT_USER = 1, 2
SELF_ID = "10000"
QQ_GROUP = 700001
QQ_USER = 20001
QQ_DM_USER = 30001


def _load_plugin_module():
    """按插件的加载方式导入（set_current_plugin 让 @service 认得 owner）"""
    from app.services.plugin import api

    spec = importlib.util.spec_from_file_location("_test_qq_napcat", str(PLUGIN_PATH))
    module = importlib.util.module_from_spec(spec)
    api.set_current_plugin("qq-napcat")
    try:
        spec.loader.exec_module(module)
    finally:
        api.set_current_plugin(None)
    return module


class FakeClient:
    """假 NapCat HTTP 客户端：只记录发出去什么（出站不真的打 HTTP）"""

    def __init__(self):
        self.sent = []

    async def send_group_msg(self, group_id, message):
        self.sent.append({"kind": "group", "target": group_id, "message": message})
        return {"status": "ok", "retcode": 0}

    async def send_private_msg(self, user_id, message):
        self.sent.append({"kind": "dm", "target": user_id, "message": message})
        return {"status": "ok", "retcode": 0}


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
            f"VALUES ({GROUP_ID}, 'NapCat 测试群', 'human', {GROUP_OWNER}, 'default', true)"
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
    plugin = module.QqNapcatPlugin()
    plugin.id = "qq-napcat"
    plugin.instance = instance
    plugin._copree_group_id = GROUP_ID
    plugin._target_agent = "浮生"
    plugin._target_user_id = AGENT_USER
    plugin._client = FakeClient()
    plugin.self_id = SELF_ID
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
            "SELECT m.sender_type, m.content, u.username, m.via "
            "FROM messages m LEFT JOIN users u ON u.id = m.sender_id "
            "WHERE m.group_id = :g ORDER BY m.id"
        ), {"g": group_id})).all()
    return rows


GROUP_EVENT = {
    "post_type": "message",
    "message_type": "group",
    "message_id": 9001,
    "group_id": QQ_GROUP,
    "user_id": QQ_USER,
    "self_id": 10000,
    "message": [
        {"type": "at", "data": {"qq": SELF_ID, "name": "机器人"}},
        {"type": "text", "data": {"text": "今天天气不错"}},
    ],
    "sender": {"user_id": QQ_USER, "nickname": "小明", "card": "小明"},
}

DM_EVENT = {
    "post_type": "message",
    "message_type": "private",
    "sub_type": "friend",
    "message_id": 8001,
    "user_id": QQ_DM_USER,
    "self_id": 10000,
    "message": [{"type": "text", "data": {"text": "在吗"}}],
    "sender": {"user_id": QQ_DM_USER, "nickname": "小红", "card": ""},
}


def test_manifest_declares_channel_kind():
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert manifest["channel"]["kind"] == "qq-napcat"

    from app.services.plugin import catalog

    assert catalog.channel_kind("qq-napcat") == "qq-napcat"


async def test_group_message_with_at_lands_and_wakes(migrated_db):
    """被 @ 的群消息：落进 Copree 群、前缀 @这个 AI、进 AI 唤醒队列"""
    from app.chat import group_delivery

    await _seed()
    plugin = await _make_plugin()
    woken = []
    original = group_delivery.wake_group_ai
    group_delivery.wake_group_ai = lambda group_id, message, content: woken.append((group_id, content))
    try:
        await plugin._on_payload(dict(GROUP_EVENT))

        rows = await _messages(GROUP_ID)
        assert len(rows) == 1, rows
        sender_type, content, username, via = rows[0]
        # 群里要能看出这条是从哪条通道来的
        assert via == "qq-napcat", rows
        assert sender_type == "human"
        # 点名前缀是桥接的一部分；入口把它归一成 id 令牌
        assert content == "<@!2> 今天天气不错", content
        assert username == "小明"                       # 说话人是谁，AI 得看得出来
        assert woken == [(GROUP_ID, "<@!2> 今天天气不错")], woken
    finally:
        group_delivery.wake_group_ai = original
        _cleanup(plugin)


async def test_group_message_without_at_lands_without_prefix(migrated_db):
    """没被 @ 的群消息：同样落库，但不加前缀、不唤醒（否则群里每句话都点名 AI）"""
    from app.chat import group_delivery

    await _seed()
    plugin = await _make_plugin()
    woken = []
    original = group_delivery.wake_group_ai
    group_delivery.wake_group_ai = lambda group_id, message, content: woken.append((group_id, content))
    try:
        await plugin._on_payload({
            **GROUP_EVENT,
            "message_id": 9002,
            "message": [{"type": "text", "data": {"text": "大家在吗"}}],
        })

        rows = await _messages(GROUP_ID)
        assert len(rows) == 1, rows
        assert rows[0][1] == "大家在吗", rows[0]
        assert rows[0][3] == "qq-napcat"
        assert woken == [], "没被 @ 的群消息不该进 AI 唤醒队列"
    finally:
        group_delivery.wake_group_ai = original
        _cleanup(plugin)


async def test_group_allowlist_blocks_other_groups(migrated_db):
    """白名单外的 QQ 群：不入库"""
    await _seed()
    plugin = await _make_plugin()
    plugin._allow = {"999999"}
    try:
        await plugin._on_payload(dict(GROUP_EVENT))
        assert await _messages(GROUP_ID) == [], "白名单外的群不该进 Copree"
    finally:
        _cleanup(plugin)


async def test_private_pairing_gate_then_approved(migrated_db):
    """默认 pairing：陌生人私聊不进 AI，只领一个配对码；主人批准之后才放行"""
    from app.database import async_session
    from app.services.plugin import pairing

    await _seed()
    plugin = await _make_plugin()
    try:
        await plugin._on_payload(dict(DM_EVENT))

        async with async_session() as db:
            count = (await db.execute(text("SELECT count(*) FROM dm_messages"))).scalar()
        assert count == 0, "没配对的人不该进入 AI 的私信"
        assert plugin._client.sent, "应该回一个配对码"
        code_msg = plugin._client.sent[0]
        assert code_msg["kind"] == "dm" and code_msg["target"] == QQ_DM_USER
        assert "配对码" in code_msg["message"]
        assert "此 AI" in code_msg["message"] and "如果你不是我的创建者" in code_msg["message"]
        assert "github.com/Coprexist/Copree" in code_msg["message"]

        async with async_session() as db:
            row = (await db.execute(text(
                "SELECT kind, origin, display_name, code, status FROM external_identities"
            ))).first()
        assert row is not None and row[0] == "qq-napcat" and row[4] == "pending", row
        assert row[1] == str(QQ_DM_USER) and row[2] == "小红", row

        # 主人批准 → 再私聊就进 AI
        async with async_session() as db:
            await pairing.approve(db, kind="qq-napcat", owner_scope="bot-a", code=row[3])
        plugin._client.sent.clear()
        await plugin._on_payload({**DM_EVENT, "message_id": 8002})
        async with async_session() as db:
            dm = (await db.execute(text(
                "SELECT m.content, u.username FROM dm_messages m JOIN users u ON u.id = m.sender_id"
            ))).first()
        assert dm is not None and dm[0] == "在吗" and dm[1] == "小红", dm

        # off：一律不处理
        plugin._dm_policy = "off"
        await plugin._on_payload({**DM_EVENT, "message_id": 8003})
        async with async_session() as db:
            count = (await db.execute(text("SELECT count(*) FROM dm_messages"))).scalar()
        assert count == 1, "off 下不该新增私信"
    finally:
        _cleanup(plugin)


async def test_group_outbound_keeps_markdown(migrated_db):
    """出站把 AI 的 Markdown 原样发出去：NapCat 侧能渲染，这正是用协议端的价值"""
    from app.chat.gm import send_gm_message
    from app.database import async_session

    await _seed()
    plugin = await _make_plugin()
    plugin._route[GROUP_ID] = {"qq": str(QQ_GROUP)}
    try:
        markdown = "**重点**：看这个\n# 标题"
        async with async_session() as db:
            await send_gm_message(db, group_id=GROUP_ID, sender_type="ai",
                                  sender_id=AGENT_USER, content=markdown)
            await db.commit()
        await _wait_sent(plugin)

        assert plugin._client.sent, "AI 回复没有发回 NapCat"
        sent = plugin._client.sent[0]
        assert sent["kind"] == "group" and sent["target"] == QQ_GROUP, sent
        assert "**" in sent["message"] and "#" in sent["message"], sent
        assert sent["message"] == markdown, sent
    finally:
        _cleanup(plugin)


async def test_outbound_mentions_become_cq_at(migrated_db):
    """出站把 <@!平台id> 翻成 [CQ:at,qq=<QQ号>]：走过这条通道的人才有号可 @，认不出的退名字。"""
    from app.chat.gm import send_gm_message
    from app.database import async_session
    from app.services.plugin.channel_user import ensure_channel_user

    await _seed()
    plugin = await _make_plugin()
    plugin._route[GROUP_ID] = {"qq": str(QQ_GROUP)}
    try:
        async with async_session() as db:
            ensured = await ensure_channel_user(
                db, kind="qq-napcat", owner_scope="bot-a", origin="123456",
                display_name="小明", origin_channel="qq-napcat",
            )
            await db.commit()
        assert ensured is not None, "锚点账号没建出来"
        uid = ensured[0]

        async with async_session() as db:
            await send_gm_message(db, group_id=GROUP_ID, sender_type="ai", sender_id=AGENT_USER,
                                  content=f"好的 <@!{uid}>，还有 <@!999999>")
            await db.commit()
        await _wait_sent(plugin)

        sent = plugin._client.sent[-1]["message"]
        assert "[CQ:at,qq=123456]" in sent, sent
        assert "999999" not in sent, sent       # 认不出的令牌不能原样发出去
    finally:
        _cleanup(plugin)


async def test_only_target_ai_group_reply_is_forwarded(migrated_db):
    """出口只转发绑定群里 AI 说的话：人说的不外流"""
    from app.chat.gm import send_gm_message
    from app.database import async_session

    await _seed()
    plugin = await _make_plugin()
    plugin._route[GROUP_ID] = {"qq": str(QQ_GROUP)}
    try:
        async with async_session() as db:
            await send_gm_message(db, group_id=GROUP_ID, sender_type="human",
                                  sender_id=GROUP_OWNER, content="人类发言")
            await db.commit()
        await asyncio.sleep(0.05)
        assert plugin._client.sent == [], "人类发言不该转发到 QQ"
    finally:
        _cleanup(plugin)


async def test_lifecycle_records_self_id(migrated_db):
    """lifecycle 事件里的 self_id 就是机器人自己的 QQ 号，@ 判定要靠它"""
    module = _load_plugin_module()
    plugin = module.QqNapcatPlugin()
    plugin.id = "qq-napcat"
    plugin.instance = "bot-a"
    await plugin._on_payload({
        "post_type": "meta_event", "meta_event_type": "lifecycle",
        "sub_type": "connect", "self_id": 12345,
    })
    assert plugin.self_id == "12345"
    status = await plugin.get_status()
    assert status["self_id"] == "12345" and status["running"] is False
    assert status["detail"] == "未运行"


def test_read_parts_covers_onebot_segments():
    module = _load_plugin_module()
    text_content, mentioned = module._read_parts([
        {"type": "at", "data": {"qq": SELF_ID, "name": "机器人"}},
        {"type": "text", "data": {"text": "看这个"}},
        {"type": "image", "data": {"file": "a.jpg"}},
        {"type": "record", "data": {}},
        {"type": "file", "data": {"name": "报表.xlsx"}},
        {"type": "at", "data": {"qq": "20001", "name": "小明"}},
        {"type": "reply", "data": {"id": "1"}},
    ], SELF_ID)
    assert mentioned is True
    assert "机器人" not in text_content          # @机器人 是唤醒信号，不重复写进正文
    assert "看这个" in text_content
    assert "[图片]" in text_content and "[语音]" in text_content
    assert "[文件] 报表.xlsx" in text_content
    assert "小明" in text_content                # @别人要保留，AI 得知道在跟谁说话

    # 没 @ 机器人时不置唤醒标记
    _text2, mentioned2 = module._read_parts(
        [{"type": "text", "data": {"text": "大家好"}}], SELF_ID
    )
    assert mentioned2 is False

    # 协议端把 message_format 配成 string 时给的是 CQ 字符串：至少别整条丢掉
    text3, mentioned3 = module._read_parts("你好啊", SELF_ID)
    assert text3 == "你好啊" and mentioned3 is False


def test_http_base_from_ws_url():
    module = _load_plugin_module()
    assert module._http_base("ws://127.0.0.1:3000") == "http://127.0.0.1:3000"
    assert module._http_base("ws://127.0.0.1:3000/ws") == "http://127.0.0.1:3000"
    assert module._http_base("wss://qq.example.com/onebot/v11/ws") == "https://qq.example.com"

async def test_multi_instance_one_channel_per_ai(migrated_db):
    """NapCat 也是多实例：一个 AI 一条通道（实例名 agent-<agentId>），配置互不串"""
    from app.database import async_session
    from app.models.plugin import Plugin
    from app.services.infrastructure.plugin_registry import PluginRegistry
    from app.services.plugin import skill_bridge
    from app.services.plugin.config import set_config

    await _seed()
    async with async_session() as db:
        db.add(Plugin(id="qq-napcat", name="QQ 通道（NapCat）", category="service", enabled=True, builtin=True))
        await db.commit()
        await set_config("qq-napcat", {"ws_url": "ws://ai-a.invalid:3000", "copree_group_id": "11", "target_agent": "浮生"}, "agent-11", db=db)
        await set_config("qq-napcat", {"ws_url": "ws://ai-b.invalid:3000", "copree_group_id": "12", "target_agent": "小满"}, "agent-12", db=db)
        await skill_bridge.apply_skill_plugins(db)

        a = PluginRegistry.get("qq-napcat:agent-11")
        b = PluginRegistry.get("qq-napcat:agent-12")
        assert a is not None and b is not None, "每个 AI 一条通道没有被建起来"
        assert a.multi_instance is True and b.multi_instance is True
        a_cfg, b_cfg = await a.config(), await b.config()
        # 平台托管协议端时地址由平台给（客户端传什么都不算）；没托管才用各自传的值。
        # 真正"每个实例各自一份"的是落点与目标 AI —— 这两项必须互不串。
        hosted_ws = os.environ.get("NAPCAT_WS_URL", "").strip()
        assert a_cfg["ws_url"] == (hosted_ws or "ws://ai-a.invalid:3000"), a_cfg
        assert b_cfg["ws_url"] == (hosted_ws or "ws://ai-b.invalid:3000"), b_cfg
        assert a_cfg["copree_group_id"] == "11" and b_cfg["copree_group_id"] == "12"
        assert PluginRegistry.keys_of("qq-napcat") == ["qq-napcat:agent-11", "qq-napcat:agent-12"]

