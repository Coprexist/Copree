"""QQ 通道插件：入站进 Copree、出站回 QQ、多实例、私聊策略、出口隔离

不起网关、不连外网——只测"消息怎么走"这一段（那是这个插件的全部逻辑所在）：
QQ 事件 → Copree（与网页端同一条投递链路）→ AI 回复 → 回到发消息的那个 QQ 群/用户。
"""
import asyncio
import importlib.util
import json
import os
import time
from pathlib import Path

from sqlalchemy import text

PLUGIN_PATH = Path(__file__).resolve().parents[1] / "plugins" / "qq-channel" / "plugin.py"
GROUP_ID = 5
GROUP_OWNER, AGENT_USER = 1, 2


def _gateway_module():
    """连接层模块：可调常量（退避、抖动）在它那儿，plugin 只是再导出。"""
    import importlib

    return importlib.import_module("copree_plugins.qq-channel.gateway")


def _plugin_module(suffix: str):
    """插件包里的子模块：拆成多文件之后，常量与纯函数按归属去对应的文件里拿。"""
    import importlib

    return importlib.import_module(f"copree_plugins.qq-channel.{suffix}")


def _load_plugin_module():
    """按生产同样的方式导入插件包（set_current_plugin 让 @service 认得 owner）。

    插件目录是包：入口之外的文件用相对导入，和产线一致；每次重导入保证用例之间不串味。
    """
    from app.services.plugin import api, packages

    api.set_current_plugin("qq-channel")
    try:
        return packages.load("qq-channel", PLUGIN_PATH.parent)
    finally:
        api.set_current_plugin(None)


class FakeClient:
    """假 QQ 客户端：只记录发出去什么"""

    def __init__(self):
        self.sent = []
        self.closed = False
        # 群成员信息接口（补拉 union_openid 用）
        self.member_calls: list = []
        # 群信息接口（群名/简介/人数）
        self.group_calls: list = []
        self.group_name = "测试真群名"
        self.group_member_num = 12
        self.group_error = ""
        # 模拟平台的单条长度上限（0 = 不限）
        self.length_limit = 0
        self.member_union = "UNION-FROM-API"
        self.member_error = ""

    async def send_group(self, group_openid, content, msg_id=None, msg_seq=1, message_reference="",
                         force_type=None):
        if self.length_limit and len(content) > self.length_limit:
            # 平台的原话：超长是整条被拒（40054007），不是截断
            raise RuntimeError(f"发 QQ 消息失败（HTTP 400）：{{'code': 40054007, 'message': '消息长度超限'}}")
        self.sent.append({"kind": "group", "target": group_openid, "content": content,
                          "msg_id": msg_id, "seq": msg_seq, "reference": message_reference,
                          "force_type": force_type})
        return {"id": "fake", "ext_info": {"ref_idx": "REFIDX-OUT"}}

    async def send_c2c(self, user_openid, content, msg_id=None, msg_seq=1, force_type=None,
                       wakeup=False):
        self.sent.append({"kind": "dm", "target": user_openid, "content": content,
                          "msg_id": msg_id, "seq": msg_seq, "force_type": force_type,
                          "wakeup": wakeup})
        return {"id": "fake"}

    async def upload_media(self, kind, target, path, file_type, file_name=""):
        """上传媒体：只记"传了哪份"（真实实现要把字节 base64 后 POST /files）"""
        digest = os.path.basename(str(path))
        self.sent.append({"kind": "upload", "target": target, "path": path,
                          "file_type": file_type, "name": file_name})
        return f"FILE-INFO:{kind}:{digest}"

    async def send_media(self, kind, target, file_info, msg_id=None, msg_seq=1, wakeup=False):
        self.sent.append({"kind": "media", "target": target, "file_info": file_info,
                          "msg_id": msg_id, "seq": msg_seq, "wakeup": wakeup})
        return {"id": "fake-media"}

    async def member_info(self, group_openid, member_openid):
        self.member_calls.append((group_openid, member_openid))
        if self.member_error:
            raise RuntimeError(self.member_error)
        return {"member_openid": member_openid, "union_openid": self.member_union}

    async def group_info(self, group_openid):
        self.group_calls.append(group_openid)
        if self.group_error:
            raise RuntimeError(self.group_error)
        return {
            "group_openid": group_openid,
            "group_name": self.group_name,
            "group_finger_memo": "", "group_class_text": "", "group_tags": [],
            "group_member_num": self.group_member_num,
        }

    async def aclose(self):
        # 停止流程会在在飞回复之后调它：顺序错了请求就打到已关闭的客户端上
        self.closed = True


async def _enable_emoji_pack(pack_id: str = "emoji") -> None:
    """测试要把表情包打开：直接改 DB 会绕过路由层的缓存失效，这里手动同步 + 失效

    以前靠"上一个用例刚把缓存填上"侥幸通过（30 秒 TTL），换一支用例顺序就红——测试不该
    依赖这种时序。见 catalog.invalidate_caches。
    """
    from app.database import async_session
    from app.models.plugin import Plugin
    from app.services.plugin import catalog

    async with async_session() as db:
        await catalog.sync_plugins_to_db(db)
        row = await db.get(Plugin, pack_id)
        if row is not None and not row.enabled:
            row.enabled = True
            await db.commit()
    catalog.invalidate_caches()


async def _seed():
    from app.database import async_session

    async with async_session() as db:
        from db_reset import clear
        await clear(db, "external_identities", "channel_wakeup_ledger",
                    "plugin_service_states", "plugin_configs", "plugins",
                    "pending_messages", "messages", "dm_messages", "dm_sessions", "group_members",
                    "groups", "users", "agents")
        # 下面用写死的 id 播种，序列要往前挪，否则插件新建用户会撞上 id=1/2
        for table in ("users", "agents", "groups", "messages", "dm_messages"):
            await db.execute(text(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), 100)"))
        await db.execute(text(
            "INSERT INTO users (id, username, password_hash, type) VALUES "
            # id=0 是生产库里本来就有的系统用户（系统通知以它发言），测试库照建一份
            "(0, '系统', 'x', 'system'), "
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
    # agents.id=7 是 _seed() 里那只 AI；账本按 agents.id 记账（_bind_target 里就是它）
    plugin._target_agent_id = 7
    plugin._target_user_id = AGENT_USER
    plugin._client = FakeClient()
    plugin._sink_handle = register_sink(
        plugin.key, group=plugin._outbound_sink, dm=plugin._dm_outbound_sink,
    )

    async def _alive():
        await asyncio.sleep(3600)

    plugin._task = asyncio.create_task(_alive())
    return plugin


def _cleanup(plugin):
    """按注册时拿到的句柄注销：按插件 id 注销会留下同名的其它实例（句柄才认得出是哪一个）"""
    from app.chat.outbound import unregister_sink

    unregister_sink(getattr(plugin, "_sink_handle", None) or plugin.key)
    if plugin._task is not None:
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


async def _group_name(group_id: int) -> str:
    from app.database import async_session

    async with async_session() as db:
        return str((await db.execute(
            text("SELECT name FROM groups WHERE id = :g"), {"g": group_id}
        )).scalar() or "")


async def _set_group_name(group_id: int, name: str, *, from_channel: bool | None = None) -> None:
    from app.database import async_session

    async with async_session() as db:
        await db.execute(text("UPDATE groups SET name = :n WHERE id = :g"), {"n": name, "g": group_id})
        if from_channel is not None:
            await db.execute(
                text("UPDATE groups SET name_from_channel = :f WHERE id = :g"),
                {"f": from_channel, "g": group_id},
            )
        await db.commit()


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


async def test_group_info_is_pulled_once_a_day(migrated_db):
    """群信息（群名/人数）按「今天第一条群消息」拉一次

    收消息是高频的、群名是低频的：每条都问等于拿限流换一个不变的答案。
    界面靠它显示真名（新加的 QQ 群默认会撞出两个同名 Copree 群，只能靠 id 分辨）。
    """
    await _seed()
    plugin = await _make_plugin()
    try:
        await plugin._on_group_at({**GROUP_EVENT, "id": "MSG-1"})
        assert plugin._client.group_calls == ["QQGROUP-AAA"], "第一条群消息要拉群信息"
        status = await plugin.get_status()
        assert status["recent_groups"][0]["name"] == "测试真群名", status["recent_groups"]
        assert status["recent_groups"][0]["member_num"] == 12

        # 同一天的第二条：不再问
        await plugin._on_group_at({**GROUP_EVENT, "id": "MSG-2", "content": "再说一句"})
        assert plugin._client.group_calls == ["QQGROUP-AAA"], "同一天不该问第二次"

        # 第二天：重新问一次（进程内按日期过期）
        plugin._group_facts["QQGROUP-AAA"]["day"] = "2000-01-01"
        await plugin._on_group_at({**GROUP_EVENT, "id": "MSG-3", "content": "第二天"})
        assert len(plugin._client.group_calls) == 2, plugin._client.group_calls
    finally:
        _cleanup(plugin)


async def test_refresh_group_facts_answers_for_a_known_group(migrated_db):
    """用户刚打开"跟随通道群名"时要现问得到：映射表里有这个群，就知道该问哪个通道群

    通道侧标识只有两条来源：映射表，或它以前来过消息（默认落点群）。都没见过就只能等消息。
    """
    await _seed()
    plugin = await _make_plugin()
    plugin._group_map = {"QQGROUP-AAA": GROUP_ID}
    try:
        facts = await plugin.refresh_group_facts(GROUP_ID)
        assert facts and facts["name"] == "测试真群名", facts
        assert plugin._client.group_calls == ["QQGROUP-AAA"]
        # 不认识的群：不瞎猜通道侧标识
        assert await plugin.refresh_group_facts(GROUP_ID + 999) is None
    finally:
        _cleanup(plugin)


async def test_group_info_failure_does_not_block_messages(migrated_db):
    """取群信息失败只是"没有名字"：消息照进 Copree（降级不能反过来挡人说话）"""
    await _seed()
    plugin = await _make_plugin()
    plugin._client.group_error = "取群信息失败（HTTP 400）：{'code': 11253}"
    try:
        await plugin._on_group_at({**GROUP_EVENT, "id": "MSG-1"})
        assert len(await _messages(GROUP_ID)) == 1, "群信息拿不到不能挡消息"
        status = await plugin.get_status()
        assert status["recent_groups"][0]["name"] == ""
        assert status["recent_groups"][0]["member_num"] == 0
    finally:
        _cleanup(plugin)


async def test_group_name_follows_the_qq_name_only_when_asked(migrated_db):
    """群设置里打开"跟随通道群名"，群名才对齐 QQ 真名；没打开的群，名字是用户的

    一个 AI 接两个 QQ 群时两个落点群默认同名，界面和 AI 都只能靠 id 分辨——
    对齐真名把"哪个是哪个"还回来；但通道不能自己往群名上写，得群主在群设置里交权。
    """
    await _seed()
    plugin = await _make_plugin()
    try:
        # 默认关：哪怕名字还是兜底名也不动
        await _set_group_name(GROUP_ID, "浮生 的群")
        await plugin._on_group_at({**GROUP_EVENT, "id": "MSG-1"})
        assert await _group_name(GROUP_ID) == "浮生 的群", "没打开开关就不该改名"

        # 打开：之后收到群消息就对齐
        await _set_group_name(GROUP_ID, "浮生 的群", from_channel=True)
        plugin._group_facts.clear()
        await plugin._on_group_at({**GROUP_EVENT, "id": "MSG-2", "content": "再一句"})
        assert await _group_name(GROUP_ID) == "测试真群名"

        # 通道侧改了名：打开的群跟着改
        plugin._client.group_name = "QQ 那边改成了别的"
        plugin._group_facts.clear()
        await plugin._on_group_at({**GROUP_EVENT, "id": "MSG-3", "content": "又一句"})
        assert await _group_name(GROUP_ID) == "QQ 那边改成了别的"

        # 关掉：名字是用户的，不再被覆盖
        await _set_group_name(GROUP_ID, "我自己起的名字", from_channel=False)
        plugin._group_facts.clear()
        await plugin._on_group_at({**GROUP_EVENT, "id": "MSG-4", "content": "最后一句"})
        assert await _group_name(GROUP_ID) == "我自己起的名字"
    finally:
        _cleanup(plugin)


class _DedupClient(FakeClient):
    """真平台的口径：同一个 (msg_id, msg_seq) 发第二次 → 40054005「消息被去重」"""

    def __init__(self):
        super().__init__()
        self.seen: set = set()
        self.deduped: list = []

    async def send_group(self, group_openid, content, msg_id=None, msg_seq=1, message_reference="",
                         force_type=None):
        if msg_id and (msg_id, msg_seq) in self.seen:
            self.deduped.append((msg_id, msg_seq, content[:20]))
            raise RuntimeError(
                "发 QQ 消息失败（HTTP 400）：{'message': '消息被去重，请检查请求msgseq', 'code': 40054005}"
            )
        if msg_id:
            self.seen.add((msg_id, msg_seq))
        await asyncio.sleep(0)          # 让并发真的交错，别靠"顺序恰好"
        return await super().send_group(group_openid, content, msg_id, msg_seq, message_reference, force_type)


async def _wait_sent_n(plugin, n: int, timeout=2.0):
    for _ in range(int(timeout / 0.01)):
        if len(plugin._client.sent) + len(getattr(plugin._client, "deduped", [])) >= n:
            return
        await asyncio.sleep(0.01)


async def test_many_replies_in_one_turn_each_get_their_own_seq(migrated_db):
    """一轮里连着回好几条（回复任务是并发的）：每条必须各占一个 seq。

    腾讯按 (msg_id, msg_seq) 去重，撞车的那条直接 40054005 丢掉——真机上就是这么丢的：
    一轮 5 条、其中第 3 条被判"消息被去重"，用户那边少一句。
    这里还要求序号连续（1..5），因为被动回复额度是按次数算的。
    """
    from app.chat.gm import send_gm_message
    from app.database import async_session

    await _seed()
    plugin = await _make_plugin()
    plugin._client = _DedupClient()
    try:
        await plugin._on_group_at(dict(GROUP_EVENT))
        async with async_session() as db:
            for i in range(5):
                await send_gm_message(db, group_id=GROUP_ID, sender_type="ai",
                                      sender_id=AGENT_USER, content=f"第{i + 1}条")
            await db.commit()
        await _wait_sent_n(plugin, 5)
        client = plugin._client
        assert not client.deduped, f"seq 撞车，被平台去重丢掉：{client.deduped}"
        assert sorted(s["seq"] for s in client.sent) == [1, 2, 3, 4, 5], client.sent
        assert all(s["msg_id"] == "MSG-1" for s in client.sent), client.sent
    finally:
        _cleanup(plugin)


class _DedupOnceClient(FakeClient):
    """模拟"这次算的号已经被用过了"：某个 seq 第一次被拒，换号之后接受"""

    def __init__(self, reject_seq=1):
        super().__init__()
        self.reject_seq = reject_seq
        self.rejected: list = []

    async def send_group(self, group_openid, content, msg_id=None, msg_seq=1, message_reference="",
                         force_type=None):
        if self.reject_seq is not None and msg_seq == self.reject_seq:
            self.rejected.append((msg_seq, content[:20]))
            self.reject_seq = None
            raise RuntimeError(
                "发 QQ 消息失败（HTTP 400）：{'message': '消息被去重，请检查请求msgseq', 'code': 40054005}"
            )
        return await super().send_group(group_openid, content, msg_id, msg_seq, message_reference, force_type)


async def test_dedup_rejection_is_retried_with_a_new_seq(migrated_db):
    """平台判「消息被去重」= 这条没收到：换一个 seq 重发，别让用户少一句话。"""
    from app.chat.gm import send_gm_message
    from app.database import async_session

    await _seed()
    plugin = await _make_plugin()
    plugin._client = _DedupOnceClient(reject_seq=1)
    try:
        await plugin._on_group_at(dict(GROUP_EVENT))
        async with async_session() as db:
            await send_gm_message(db, group_id=GROUP_ID, sender_type="ai",
                                  sender_id=AGENT_USER, content="只有这一条")
            await db.commit()
        await _wait_sent(plugin)
        client = plugin._client
        assert client.rejected, "第一次应当被平台判去重"
        assert [s["seq"] for s in client.sent] == [2], client.sent
        assert plugin.last_error == "", plugin.last_error
        # 被拒的那次也占了号：下一个 seq 不能是 2
        assert plugin._routes["QQGROUP-AAA"]["seq"] == 2
    finally:
        _cleanup(plugin)


async def test_prep_runs_concurrently_and_send_waits_its_turn(migrated_db):
    """准备并发、发送排队——用事件卡住第一条，验证两条性质：

    1. 第一条还卡在准备阶段时，后面两条的准备**已经各自跑完了**（准备不互相等）；
    2. 此时**一条都没发出去**（后面的准备完了也不许越过前面那条），放行后按主站顺序依次到达。

    用事件而不是 sleep 的时长：时间数字会让用例时红时绿，事件让"卡住"这件事确定发生。
    """
    from app.chat.gm import send_gm_message
    from app.database import async_session

    await _seed()
    plugin = await _make_plugin()
    release_first = asyncio.Event()
    others_prepared = asyncio.Event()
    prepared: list[str] = []
    original = plugin._translate_mentions

    async def gated(text, *, plain=False):
        if "第一条" in text:
            prepared.append("first-start")
            await release_first.wait()          # 第一条一直停在准备阶段
            prepared.append("first-done")
        else:
            prepared.append(text[:3])
            if len([p for p in prepared if p != "first-start"]) >= 2:
                others_prepared.set()
        return await original(text, plain=plain)

    plugin._translate_mentions = gated
    try:
        await plugin._on_group_at(dict(GROUP_EVENT))
        texts = ["第一条", "第二条", "第三条"]
        async with async_session() as db:
            for t in texts:
                await send_gm_message(db, group_id=GROUP_ID, sender_type="ai",
                                      sender_id=AGENT_USER, content=t)
            await db.commit()
        await asyncio.wait_for(others_prepared.wait(), timeout=2)
        assert plugin._client.sent == [], "前面那条还没准备好，后面的先发出去了"
        release_first.set()
        await _wait_sent_n(plugin, 3)
        assert [s["content"] for s in plugin._client.sent] == texts, plugin._client.sent
        assert prepared[0] == "first-start" and "first-done" in prepared, prepared
    finally:
        release_first.set()                     # 断言失败时别把准备任务吊死
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
        assert sent["force_type"] is None, "留空 = 默认：先 Markdown、没权限降级纯文本"
    finally:
        _cleanup(plugin)


async def test_body_format_pins_private_chat_too(migrated_db):
    """正文格式是通道级的：固定成纯文本后私聊也按它发。

    此前只有群回复读这个配置，私聊永远"先 Markdown 后降级"——
    同一个开关，群消息成了纯文本、私聊还是 Markdown。
    """
    from app.chat.dm import send_dm_message
    from app.database import async_session

    await _seed()
    plugin = await _make_plugin()
    plugin._dm_policy = "open"
    plugin._msg_type = 0                      # 配置里选了「纯文本」
    try:
        await plugin._on_c2c(dict(DM_EVENT))
        async with async_session() as db:
            session_id = (await db.execute(text("SELECT session_id FROM dm_sessions"))).scalar()
            await send_dm_message(db, session_id, sender_id=AGENT_USER, content="**重点**：在的")
            await db.commit()
        await _wait_sent(plugin)

        sent = plugin._client.sent[-1]
        assert sent["kind"] == "dm", sent
        assert sent["force_type"] == 0, sent      # 固定成文本，不再"先 Markdown"
    finally:
        _cleanup(plugin)


async def test_dm_pairing_gate_then_approved(migrated_db):
    """默认 pairing：陌生人私聊不进 AI，只领一个配对码；主人批准之后才放行"""
    from app.database import async_session
    from app.services.channel import identity

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
        # 文案是两条通道共用的一份：本 AI + 创建者提示 + 开源地址
        assert "此 AI" in code_msg and "如果你不是我的创建者" in code_msg
        assert "github.com/Coprexist/Copree" in code_msg

        async with async_session() as db:
            row = (await db.execute(text(
                "SELECT kind, origin, display_name, code, status FROM external_identities"
            ))).first()
        assert row is not None and row[0] == "qq" and row[4] == "pending" and row[2] == "小红", row

        # 主人批准 → 再私聊就进 AI
        async with async_session() as db:
            await identity.approve(db, kind="qq", owner_scope="bot-a", code=row[3])
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
            await set_config("qq-channel", {"app_id": "111", "client_secret": "s1",
                                            "target_agent_id": "7", "target_agent": "浮生"},
                             "bot-a", db=db)
            await set_config("qq-channel", {"app_id": "222", "client_secret": "s2",
                                            "target_agent_id": "7", "target_agent": "浮生"},
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
        plugin._routes["QQGROUP-AAA"] = {"copree_group_id": GROUP_ID, "qq": "QQGROUP-AAA", "msg_id": "MSG-1", "seq": 0, "ts": 0}
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

        plugin._routes["QQGROUP-AAA"] = {"copree_group_id": GROUP_ID, 
            "qq": "QQGROUP-AAA", "msg_id": "MSG-1", "seq": 0, "ts": time.time(),
            "peer_name": "小明", "peer_openid": "OPENID-XYZ",
        }
        result = await plugin.self_test()
        assert result["sent"] is True and result["mode"] == "passive", result
        assert result["response"]["id"] == "fake", result
        sent = plugin._client.sent[-1]
        assert sent["kind"] == "group" and sent["msg_id"] == "MSG-1" and sent["seq"] == 1, sent
        assert "<@!OPENID-XYZ>" in sent["content"] and "@小明" in sent["content"], sent

        plugin._routes["QQGROUP-AAA"]["ts"] = 0                      # 窗口早就过期
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
    import time

    plugin._routes["QQGROUP-AAA"] = {"copree_group_id": GROUP_ID, "qq": "QQGROUP-AAA", "msg_id": "MSG-1", "peer_name": "小明",
                               "ts": time.time(), "at_event": True}

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
        assert plugin._routes["QQGROUP-AAA"].get("at_event") is False, plugin._routes["QQGROUP-AAA"]

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

        # @ 的是**别的机器人**（is_you=false）：这不是点名我，不加唤醒令牌
        # （2026-10-05 真机：书爱 @ 绵绵，涵吾珑被这条叫醒了）
        await plugin._on_group_message({**GROUP_EVENT, "id": "FULL-2", "content": "你看这个",
                                        "mentions": [{"id": "OTHER-BOT", "bot": True, "is_you": False,
                                                      "username": "绵绵"}]})
        rows = await _messages(GROUP_ID)
        # 没点到我 → 不带**我的**唤醒令牌；被 @ 的那个机器人自己得看得见（补它的令牌）
        assert len(rows) == 2 and not rows[1][2].startswith("<@!2>"), rows
        assert "<@!" in rows[1][2] and rows[1][2].endswith("你看这个"), rows

        await plugin._on_group_message({**GROUP_EVENT, "id": "FULL-3", "content": "你看这个",
                                        "mentions": [{"id": "BOT-OPENID", "bot": True, "is_you": True}]})
        rows = await _messages(GROUP_ID)
        assert len(rows) == 3 and rows[2][2].startswith("<@!2>"), rows  # 点名我 → 带唤醒令牌
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
    plugin._routes["QQGROUP-AAA"] = {"copree_group_id": GROUP_ID, "qq": "QQGROUP-AAA", "msg_id": "MSG-1", "seq": 0, "ts": time.time()}
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
    plugin._routes["QQGROUP-AAA"] = {"copree_group_id": GROUP_ID, "qq": "QQGROUP-AAA", "msg_id": "MSG-1", "seq": 0, "ts": time.time()}
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
async def test_same_text_from_two_instances_still_merges_into_one_row(migrated_db):
    """同一句人话被两台机器人并发送进来，只能落一条。

    认亲是"先查后写"：事务级咨询锁的键必须与实例无关，否则每台各锁各的 key，
    谁也没等谁，各查各的都查不到对方未提交的那条，同一句话就落好几遍
    （2026-10-31 真机：群里同一条消息出现三份）。
    """
    from app.database import async_session

    await _seed()
    bot_a = await _make_plugin("bot-a")
    bot_b = await _make_plugin("bot-b")
    try:
        await asyncio.gather(
            bot_a._on_group_at({**GROUP_EVENT, "id": "DUP-1", "content": "同一句话先说一遍",
                                "message_scene": {"source": "default", "ext": ["msg_idx=REFIDX-A"]}}),
            bot_b._on_group_at({**GROUP_EVENT, "group_openid": "QQGROUP-BBB", "id": "DUP-2",
                                "content": "同一句话先说一遍",
                                "message_scene": {"source": "default", "ext": ["msg_idx=REFIDX-B"]}}),
        )
        async with async_session() as db:
            total = (await db.execute(text(
                "SELECT count(*) FROM messages WHERE group_id = :g"), {"g": GROUP_ID})).scalar()
        assert total == 1, total
    finally:
        _cleanup(bot_a)
        _cleanup(bot_b)


async def test_merge_keeps_the_mentioned_instances_own_token(migrated_db):
    """合并时把"点到了我"补回正文：另一台先落库、令牌是它渲染的，被 @ 的那台得看得见自己。

    2026-10-31 真机：三台机器人各自落了一条，@ 鲸鱼娘 的那条正文里是别台补的影子 id，
    她判定"没点我"，整轮沉默。
    """
    from app.database import async_session

    await _seed()
    bot_a = await _make_plugin("bot-a")
    bot_b = await _make_plugin("bot-b")
    try:
        # 另一台先收到：@ 的是"另一个机器人"，它认不出，按外部人建了锚点
        await bot_a._on_group_message({
            **GROUP_EVENT, "id": "MENTION-1", "content": "你再发一下……",
            "mentions": [{"id": "BOT-OPENID-W", "bot": True, "is_you": False,
                          "username": "鲸鱼娘 Deepseek"}],
        })
        # 被 @ 的那台随后收到同一句：事件里 is_you=True，补的是它自己的账号
        await bot_b._on_group_message({
            **GROUP_EVENT, "group_openid": "QQGROUP-BBB", "id": "MENTION-2",
            "content": "你再发一下……",
            "mentions": [{"id": "SELF-OPENID", "bot": True, "is_you": True}],
        })
        rows = await _messages(GROUP_ID)
        assert len(rows) == 1, rows
        assert rows[0][2].startswith(f"<@!{AGENT_USER}>"), rows[0][2]
    finally:
        _cleanup(bot_a)
        _cleanup(bot_b)
async def test_twin_merge_keeps_each_instances_own_channel_ids(migrated_db):
    """同一句话被两台机器人送进来只落一条，但**通道标识按实例各存一份**。

    腾讯按机器人隔离通道 id：A 的 REFIDX 在 B 那里指向别的东西。合并消息时只留一份，
    回话的人就会拿别人的索引去引用——2026-10-31 真机 40034138「引用消息越权」，整条发不出去。
    """
    from app.chat.gm import send_gm_message
    from app.database import async_session

    await _seed()
    plugin_a = await _make_plugin("bot-a")
    plugin_b = await _make_plugin("bot-b")
    try:
        for plugin, tag in ((plugin_a, "A"), (plugin_b, "B")):
            await plugin._on_group_at({
                **GROUP_EVENT, "id": f"Q-{tag}", "content": "今晚咱们谁先说",
                "message_scene": {"source": "default", "ext": [f"msg_idx=REFIDX-{tag}"]},
            })
        async with async_session() as db:
            total = (await db.execute(text(
                "SELECT count(*) FROM messages WHERE group_id = :g"), {"g": GROUP_ID})).scalar()
            refs = (await db.execute(text(
                "SELECT r.instance, r.channel_ref_idx FROM message_channel_refs r "
                "JOIN messages m ON m.id = r.message_id WHERE m.group_id = :g "
                "ORDER BY r.instance"), {"g": GROUP_ID})).all()
            merged = int((await db.execute(text(
                "SELECT id FROM messages WHERE group_id = :g ORDER BY id DESC LIMIT 1"
            ), {"g": GROUP_ID})).scalar())
        assert total == 1, total          # 合并成一条，不重复冒泡
        assert [tuple(r) for r in refs] == [("bot-a", "REFIDX-A"), ("bot-b", "REFIDX-B")], refs

        # 让 bot-b 来发这条回复（bot-a 当作掉线）：引用必须是它自己的索引
        plugin_a._task.cancel()
        await asyncio.sleep(0)
        plugin_b._client.sent.clear()
        async with async_session() as db:
            await send_gm_message(db, group_id=GROUP_ID, sender_type="ai", sender_id=AGENT_USER,
                                  content="那我先说", reply_to=merged)
            await db.commit()
        await _wait_sent(plugin_b)
        assert plugin_b._client.sent[-1]["reference"] == "REFIDX-B", plugin_b._client.sent[-1]
    finally:
        _cleanup(plugin_a)
        _cleanup(plugin_b)
async def test_inbound_quote_resolves_a_ref_recorded_by_another_instance(migrated_db):
    """引用的是别的实例发出去的那条：引用对象按群里的消息认，不按哪台先看到。

    2026-10-31 真机：书爱引用鲸鱼娘那句「D老师好看这事我认」再 @ 她，站内却没记成引用——
    被引用的那条是 agent-49 发出去的（REFIDX + 会话都在它那侧），这句话却从另一台机器人的
    副本进来了，按 (实例, 会话) 精确匹配必然找不到。
    """
    from sqlalchemy import text

    from app.chat.gm import send_gm_message
    from app.database import async_session

    await _seed()
    plugin = await _make_plugin()
    plugin._copree_group_id = GROUP_ID
    try:
        async with async_session() as db:
            # 另一台实例发出去的消息：只有老列上的 REFIDX，本实例没有它的分实例行
            await db.execute(text(
                "INSERT INTO messages (group_id, sender_type, sender_id, content, channel_ref_idx, channel_origin) "
                "VALUES (:g, 'ai', 3, 'D老师好看这事我认', 'REFIDX-OTHER', 'qq:bot-other:QQGROUP-BBB')"
            ), {"g": GROUP_ID})
            await db.commit()

        await plugin._on_group_message({
            **GROUP_EVENT, "id": "QUOTE-1", "content": "我说你羞涩了欸",
            "message_type": 103,
            "msg_elements": [{"message_type": 1, "content": "D老师好看这事我认"}],
            "message_scene": {"source": "default", "ext": ["msg_idx=REFIDX-IN", "ref_msg_idx=REFIDX-OTHER"]},
        })
        async with async_session() as db:
            row = (await db.execute(text(
                "SELECT id, reply_to FROM messages WHERE group_id = :g ORDER BY id DESC LIMIT 1"
            ), {"g": GROUP_ID})).first()
            quoted = (await db.execute(text(
                "SELECT id FROM messages WHERE channel_ref_idx = 'REFIDX-OTHER'"
            ))).scalar()
        assert row is not None and int(row[1] or 0) == int(quoted), row
    finally:
        _cleanup(plugin)
async def test_quote_is_dropped_when_the_quoted_message_is_from_another_qq_group(migrated_db):
    """被引用的那条来自另一个 QQ 群时不许带引用：REFIDX 只在它自己那个群里有效。

    2026-10-31 真机：一个 Copree 群接了好几台机器人，各自的 QQ 群不同；一句话来自甲群、
    出站路由却落在乙群，腾讯回「引用消息越权」(40034138) 把整条消息拒了——
    用户看到的就是「鲸鱼娘发不出消息」。引用是锦上添花，宁可不带也不能发不出去。
    """
    from sqlalchemy import text

    from app.chat.gm import send_gm_message
    from app.database import async_session

    await _seed()
    plugin = await _make_plugin()
    plugin._copree_group_id = GROUP_ID
    try:
        await plugin._on_group_at({
            **GROUP_EVENT, "id": "Q-2", "content": "问题",
            "message_scene": {"source": "default", "ext": ["msg_idx=REFIDX-FOREIGN"]},
        })
        async with async_session() as db:
            inbound = (await db.execute(text(
                "SELECT id FROM messages WHERE group_id = :g ORDER BY id DESC LIMIT 1"
            ), {"g": GROUP_ID})).first()
            assert inbound is not None
            # 老列与按实例的表一起挪走：模拟"本实例在本会话里从没见过这条消息"，
            # 它手里的 REFIDX 只属于另一个会话
            await db.execute(text(
                "UPDATE messages SET channel_origin = :o WHERE id = :i"
            ), {"o": "qq:bot-a:ANOTHER-GROUP-OPENID", "i": int(inbound[0])})
            await db.execute(text(
                "DELETE FROM message_channel_refs WHERE message_id = :i"
            ), {"i": int(inbound[0])})
            await db.commit()

        plugin._client.sent.clear()
        async with async_session() as db:
            await send_gm_message(db, group_id=GROUP_ID, sender_type="ai", sender_id=AGENT_USER,
                                  content="回答", reply_to=int(inbound[0]))
            await db.commit()
        await _wait_sent(plugin)
        sent = plugin._client.sent[-1]
        assert sent["content"] == "回答", sent        # 消息本身必须发出去
        assert sent["reference"] == "", sent          # 但不许带越权的引用
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


async def test_mentioned_other_bot_resolves_to_its_real_account(migrated_db):
    """@ 到另一台机器人：补的是**它的真账号**，不是给它建影子账号

    QQ 按 appid 发 openid，A 侧看到的那个名字与它自己的账号 id 对不上；照旧当外部人
    建锚点，群里就多出一个影子账号，AI 看到的 @ 指到假 id 上（2026-10-05 真机：
    补出来的那个 id，被 @ 的那台机器人自己根本不认）。
    """
    from app.database import async_session

    await _seed()
    bot_a = await _make_plugin("bot-a")
    try:
        async with async_session() as db:
            await db.execute(text(
                "INSERT INTO users (id, username, password_hash, type) VALUES (3, '小蓝', 'x', 'ai')"
            ))
            await db.execute(text(
                "INSERT INTO agents (id, owner_id, name, user_id, discoverable) "
                "VALUES (8, 1, '小蓝', 3, true)"
            ))
            await db.execute(text(
                "INSERT INTO group_members (group_id, member_type, member_id, role) "
                f"VALUES ({GROUP_ID}, 'ai', 3, 'member')"
            ))
            await db.commit()

        await bot_a._on_group_message({
            **GROUP_EVENT, "id": "BOT-MENTION-1", "content": "你呢？",
            "mentions": [{"id": "BOT-OPENID-B", "bot": True, "is_you": False, "username": "小蓝"}],
        })

        rows = await _messages(GROUP_ID)
        assert len(rows) == 1, rows
        assert rows[0][2] == "<@!3> 你呢？", rows[0][2]
        async with async_session() as db:
            bridged = (await db.execute(text(
                "SELECT count(*) FROM external_identities WHERE origin = 'BOT-OPENID-B'"
            ))).scalar()
        assert bridged == 0, "认出来是自己人了，就不该再建影子账号"
    finally:
        _cleanup(bot_a)


async def test_bot_mention_heals_the_stale_shadow_account(migrated_db):
    """认回真身时把它在这个实例下的旧影子账号并进来：绑定收敛回本体，@ 不再指向假 id。

    2026-10-31 真机：认不出自己人的年代留下了 (实例, openid) → external 账号的绑定，
    之后 @ 这台机器人一直解析到影子身上，被 @ 的那台判成「没点我」。
    """
    from app.database import async_session

    await _seed()
    bot_a = await _make_plugin("bot-a")
    try:
        async with async_session() as db:
            await db.execute(text(
                "INSERT INTO users (id, username, password_hash, type) VALUES "
                "(3, '鲸鱼娘 DSH', 'x', 'ai'), (30, '鲸鱼娘 Deepseek#2', 'x', 'external')"
            ))
            await db.execute(text(
                "INSERT INTO agents (id, owner_id, name, user_id, discoverable) "
                "VALUES (8, 1, '鲸鱼娘 Deepseek', 3, true)"
            ))
            await db.execute(text(
                "INSERT INTO group_members (group_id, member_type, member_id, role) "
                f"VALUES ({GROUP_ID}, 'ai', 3, 'member')"
            ))
            # 历史遗留：这台机器人的 openid 早先被绑成了影子账号
            await db.execute(text(
                "INSERT INTO external_identities (kind, owner_scope, origin, user_id, display_name) "
                "VALUES ('qq', 'bot-a', 'BOT-OPENID-W', 30, '鲸鱼娘 Deepseek')"
            ))
            await db.commit()

        await bot_a._on_group_message({
            **GROUP_EVENT, "id": "HEAL-1", "content": "你再发一下……",
            "mentions": [{"id": "BOT-OPENID-W", "bot": True, "is_you": False,
                          "username": "鲸鱼娘 Deepseek"}],
        })

        rows = await _messages(GROUP_ID)
        assert len(rows) == 1, rows
        assert rows[0][2] == "<@!3> 你再发一下……", rows[0][2]   # 补的是本体 id
        async with async_session() as db:
            bound = (await db.execute(text(
                "SELECT user_id FROM external_identities WHERE origin = 'BOT-OPENID-W'"
            ))).scalar()
            shadow_left = (await db.execute(text("SELECT count(*) FROM users WHERE id = 30"))).scalar()
        assert bound == 3, bound          # 绑定已改指本体
        assert shadow_left == 0, shadow_left   # 空壳已删
    finally:
        _cleanup(bot_a)
async def test_bot_mention_by_agent_name_reaches_the_bot_itself(migrated_db):
    """机器人的 QQ 昵称是 agent 名，可能与账号名不同：按成员名认不出来时按 agent 名再认一次。

    2026-10-31 真机：agent「鲸鱼娘 Deepseek」/ 账号「鲸鱼娘 DSH」，@ 之后被当成外部人
    建了个「鲸鱼娘 Deepseek#2」的影子账号，补出来的 <@!177> 那台机器人自己不认识，
    于是它判定"没被点名"，整轮沉默。
    """
    from app.database import async_session

    await _seed()
    bot_a = await _make_plugin("bot-a")
    try:
        async with async_session() as db:
            await db.execute(text(
                "INSERT INTO users (id, username, password_hash, type) VALUES (3, '鲸鱼娘 DSH', 'x', 'ai')"
            ))
            await db.execute(text(
                "INSERT INTO agents (id, owner_id, name, user_id, discoverable) "
                "VALUES (8, 1, '鲸鱼娘 Deepseek', 3, true)"
            ))
            await db.execute(text(
                "INSERT INTO group_members (group_id, member_type, member_id, role) "
                f"VALUES ({GROUP_ID}, 'ai', 3, 'member')"
            ))
            await db.commit()

        await bot_a._on_group_message({
            **GROUP_EVENT, "id": "BOT-MENTION-2", "content": "你再发一下……",
            "mentions": [{"id": "BOT-OPENID-WHALE", "bot": True, "is_you": False,
                          "username": "鲸鱼娘 Deepseek"}],
        })

        rows = await _messages(GROUP_ID)
        assert len(rows) == 1, rows
        assert rows[0][2] == "<@!3> 你再发一下……", rows[0][2]
        async with async_session() as db:
            shadow = (await db.execute(text(
                "SELECT count(*) FROM external_identities WHERE origin = 'BOT-OPENID-WHALE'"
            ))).scalar()
        assert shadow == 0, "认出是自己人了，不该再建影子账号"
    finally:
        _cleanup(bot_a)
async def test_bot_mention_with_a_human_name_does_not_hit_that_human(migrated_db):
    """同名的真人不算"自己人"：昵称对上还得看这个成员本身是不是 AI

    重名很常见（群里就有个叫小蓝的人）。只看名字会把 @ 喊到人身上；
    认不出来也不否决——照旧当外部人建锚点账号，@ 了谁照样看得见。
    """
    from app.database import async_session

    await _seed()
    bot_a = await _make_plugin("bot-a")
    try:
        async with async_session() as db:
            await db.execute(text(
                "INSERT INTO users (id, username, password_hash, type) VALUES (4, '小蓝', 'x', 'human')"
            ))
            await db.execute(text(
                "INSERT INTO group_members (group_id, member_type, member_id, role) "
                f"VALUES ({GROUP_ID}, 'human', 4, 'member')"
            ))
            await db.commit()

        await bot_a._on_group_message({
            **GROUP_EVENT, "id": "BOT-MENTION-2", "content": "你呢？",
            "mentions": [{"id": "BOT-OPENID-C", "bot": True, "is_you": False, "username": "小蓝"}],
        })

        rows = await _messages(GROUP_ID)
        assert len(rows) == 1, rows
        assert "<@!4>" not in rows[0][2], f"同名真人不该被当成那个机器人：{rows[0][2]}"
        async with async_session() as db:
            anchor = (await db.execute(text(
                "SELECT id FROM users WHERE email = 'BOT-OPENID-C@qq.bridge'"
            ))).scalar()
        assert anchor, "认不出来时回落成外部人锚点（不否决）"
        assert f"<@!{anchor}>" in rows[0][2], rows[0][2]
    finally:
        _cleanup(bot_a)


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
        # 业务幂等锚点在 ref 上（flags 是平台语义位，插件不碰）；通知走 api.report_notice
        assert len(entries) == 1 and entries[0]["ref"] == "channel_mode:full", entries
        assert entries[0]["flags"] == {}, "长期通知不带 drop_on_unlock"
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
        assert len(entries) == 2 and entries[1]["ref"] == "channel_mode:at", entries
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


async def test_full_mode_mention_adopts_the_same_person_across_bots(migrated_db):
    """同一个人在不同机器人下是不同 openid：第一次 @ 到时按同名并回已有账号。

    QQ 的 openid 按应用发；不认亲就会在同一个群里出现「周庶人」「周庶人#2」。
    窄口径：只在地址第一次出现、新账号还没说过话、群里同名外部账号恰好一个时合并。
    """
    from app.database import async_session
    from app.models.group import GroupMember
    from app.models.user import User

    await _seed()
    plugin = await _make_plugin()
    plugin._copree_group_id = GROUP_ID
    try:
        async with async_session() as db:
            person = User(username="小红", password_hash="x", type="external", origin_channel="qq")
            db.add(person)
            await db.flush()
            existing_id = int(person.id)
            db.add(GroupMember(group_id=GROUP_ID, member_type="human",
                               member_id=existing_id, role="member"))
            await db.commit()

        await plugin._on_group_message({
            **GROUP_EVENT, "id": "ADOPT-1",
            "content": "欢迎 <@OPENID-OTHER-BOT> 喵",
            "mentions": [{"id": "OPENID-OTHER-BOT", "username": "小红"}],
        })
        rows = await _messages(GROUP_ID)
        stored = rows[0][2]
        assert f"<@!{existing_id}>" in stored, stored      # 令牌指向已有账号

        async with async_session() as db:
            names = (await db.execute(text(
                "SELECT username FROM users WHERE username LIKE '小红%'"
            ))).scalars().all()
            identities = (await db.execute(text(
                "SELECT count(*) FROM external_identities WHERE user_id = :u"
            ), {"u": existing_id})).scalar()
        assert names == ["小红"], names                   # 没有多出「小红#2」
        assert int(identities) == 1, identities
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

    # 私聊走同一个发送层：固定成文本就只发一次，不再先试 Markdown
    await client.send_c2c("OPENID-DM", "**粗体** 与 `代码`", msg_id="M-2", msg_seq=1, force_type=0)
    assert sent[2]["msg_type"] == 0, sent
    assert "**" not in sent[2]["content"] and "`代码`" not in sent[2]["content"], sent
    assert sent[2]["msg_id"] == "M-2", sent


async def test_reply_task_drained_before_client_close(migrated_db):
    """回复任务要有强引用，且 stop() 先等它跑完再关客户端

    与 NapCat 通道共用同一份托管（services/plugin/tasks.py），这里验的是接线：
    两个坑都不挑协议，官方通道同样会中。
    """
    from app.chat.gm import send_gm_message
    from app.chat.outbound import unregister_sink
    from app.database import async_session

    await _seed()
    plugin = await _make_plugin()
    client = plugin._client
    finished = []

    async def _slow_post(route, kind, text, reference_id, message_id, attachments=()):
        await asyncio.sleep(0.05)
        finished.append(text)

    plugin._post_reply = _slow_post
    try:
        await plugin._on_group_at(dict(GROUP_EVENT))          # 先建好回哪个群的路由
        async with async_session() as db:
            await send_gm_message(db, group_id=GROUP_ID, sender_type="ai",
                                  sender_id=AGENT_USER, content="慢回复")
            await db.commit()
        await asyncio.sleep(0.01)
        assert len(plugin._replies) >= 1, "回复任务没有被持有（随时可能被 GC 回收）"
        assert finished == [] and client.closed is False

        await plugin.stop()
        assert finished == ["慢回复"], "stop() 没有等在飞的回复"
        assert client.closed is True
    finally:
        unregister_sink(getattr(plugin, "_sink_handle", None) or plugin.key)

# ── 引用消息：QQ 的 message_type=103 与站内的 reply_to 对不对得上 ─────────────

def test_readable_content_humanizes_qq_face_marks():
    """腾讯把表情发成 content 里的标记（ext 是 base64 的 JSON，带表情自己的文本）。

    原样入库的话，界面上是一串标记、AI 也读不出情绪，所以入站要还原成可读文本。
    """
    module = _load_plugin_module()
    plugin = module.QqChannelPlugin()

    assert plugin._readable_content(
        {"content": '你还能信用我的话吗<faceType=1,faceId="6",ext="eyJ0ZXh0Ijoi5a6z576eIn0=">'}
    ) == "你还能信用我的话吗[表情:害羞]"
    assert plugin._readable_content(
        {"content": '<faceType=3,faceId="312",ext="不是base64">'}
    ) == "[表情]", "解不出的 ext 退成占位，别把半截标记留在正文里"
    assert plugin._readable_content({"content": "纯文本"}) == "纯文本"


def test_quoted_of_reads_msg_elements_and_ref_idx():
    """引用消息的内容在 msg_elements 里，被引用那条的索引在 scene.ext 的 ref_msg_idx 上"""
    module = _load_plugin_module()
    plugin = module.QqChannelPlugin()

    assert plugin._quoted_of({
        "message_type": 103,
        "content": "你还能信用我的话吗",
        "msg_elements": [{"message_type": 103, "content": "晚安"}],
        "message_scene": {"ext": ["auth_token=SECRET", "msg_idx=IDX-2", "ref_msg_idx=IDX-1"]},
    }) == ("晚安", "IDX-1")

    assert plugin._quoted_of({"message_type": 0, "content": "普通消息"}) == ("", ""), \
        "普通消息不该被当成引用"
    assert plugin._quoted_of({"message_type": "x", "msg_elements": []}) == ("", ""), \
        "message_type 是脏值也不能把入站带崩"


def _quote_event(msg_id: str, *, content: str, quoted: str, ref_idx: str, my_idx: str) -> dict:
    """一条 QQ 引用消息事件：正文 + msg_elements（被引用的内容）+ ref_msg_idx（被引用那条的索引）"""
    return {
        **GROUP_EVENT, "id": msg_id, "content": content, "mentions": [],
        "message_type": 103,
        "msg_elements": [{"message_type": 103, "content": quoted}],
        "message_scene": {"ext": [f"msg_idx={my_idx}", f"ref_msg_idx={ref_idx}"]},
    }


async def _messages_with_quote(group_id: int):
    from app.database import async_session

    async with async_session() as db:
        return (await db.execute(text(
            "SELECT id, content, reply_to, channel_ref_idx FROM messages "
            "WHERE group_id = :g ORDER BY id"
        ), {"g": group_id})).all()


async def _wait_channel_ref(message_id: int, timeout: float = 2.0) -> str | None:
    """出站记 REFIDX 是后台任务里的第二次写库，等它落下来"""
    from app.database import async_session

    for _ in range(int(timeout / 0.05)):
        async with async_session() as db:
            value = (await db.execute(text(
                "SELECT channel_ref_idx FROM messages WHERE id = :i"
            ), {"i": message_id})).scalar()
        if value:
            return str(value)
        await asyncio.sleep(0.05)
    return None


async def test_qq_quote_becomes_a_copree_quote(migrated_db):
    """QQ 里引用某条回过来 → 站内那条也画成引用块。

    两端靠同一个 REFIDX 对上：入站把它自己的 msg_idx 写在 channel_ref_idx 上、出站把
    ext_info.ref_idx 写在同一个字段上，所以"被引用的是哪条"一条 where 就找得回来。
    """
    await _seed()
    await _enable_emoji_pack()      # 入站的 <face…> 会变成 [表情:害羞]，归一要有包可查
    plugin = await _make_plugin()
    plugin._copree_group_id = GROUP_ID
    try:
        await plugin._on_group_message({
            **GROUP_EVENT, "id": "Q-1", "content": "今天天气不错", "mentions": [],
            "message_scene": {"ext": ["msg_idx=IDX-1"]},
        })
        await plugin._on_group_message(_quote_event(
            "Q-2",
            content='你还能信用我的话吗<faceType=1,faceId="6",ext="eyJ0ZXh0Ijoi5a6z576eIn0=">',
            quoted="今天天气不错", ref_idx="IDX-1", my_idx="IDX-2",
        ))

        rows = await _messages_with_quote(GROUP_ID)
        assert len(rows) == 2, rows
        assert rows[0][3] == "IDX-1", "入站要把自己的 msg_idx 记在 channel_ref_idx 上"
        assert rows[1][2] == rows[0][0], f"引用要落到站内那条消息上：{rows[1]}"
        # QQ 的表情标记入站 → 入口归一：图片表情落成附件，正文只留字（库里不再留自造写法）
        assert rows[1][1] == "你还能信用我的话吗", rows[1][1]
    finally:
        _cleanup(plugin)


async def test_bare_quote_still_lands_and_carries_the_quoted_text(migrated_db):
    """只引用、一个字没打：也要落库。

    正文带上被引用的原文——引用块只给人看，AI 读的是正文，不带它就接不住"他指的是哪句"。
    """
    await _seed()
    plugin = await _make_plugin()
    plugin._copree_group_id = GROUP_ID
    try:
        await plugin._on_group_message({
            **GROUP_EVENT, "id": "Q-1", "content": "今天天气不错", "mentions": [],
            "message_scene": {"ext": ["msg_idx=IDX-1"]},
        })
        await plugin._on_group_message(_quote_event(
            "Q-2", content="", quoted="今天天气不错", ref_idx="IDX-1", my_idx="IDX-2",
        ))

        rows = await _messages_with_quote(GROUP_ID)
        assert len(rows) == 2, rows
        assert rows[1][1] == "[引用] 今天天气不错", rows[1][1]
        assert rows[1][2] == rows[0][0], rows[1]
    finally:
        _cleanup(plugin)


async def test_quote_of_a_message_we_never_saw_stays_unquoted(migrated_db):
    """被引用的那条不在库里（机器人当时不在线）→ 不猜 id，只把正文落下来"""
    await _seed()
    plugin = await _make_plugin()
    plugin._copree_group_id = GROUP_ID
    try:
        await plugin._on_group_message(_quote_event(
            "Q-1", content="你还能信用我的话吗", quoted="很久以前的一条",
            ref_idx="IDX-不存在", my_idx="IDX-1",
        ))

        rows = await _messages_with_quote(GROUP_ID)
        assert len(rows) == 1, rows
        assert rows[0][1] == "你还能信用我的话吗", rows[0][1]
        assert rows[0][2] is None, "找不到就别猜一个 id，引用错人比不引用更糟"
    finally:
        _cleanup(plugin)


async def test_emoji_markers_do_not_leak_internal_syntax_to_qq(migrated_db):
    """站内的表情写法发到 QQ 前要摘干净。

    内置包只有图：图片表情在上游入口就变成附件了（见 catalog.take_image_emoji_attachments），
    走到这里的是"没启用包"或历史消息留下的写法。对不上的若原样发，QQ 那边看到的是
    [表情:某某] / :shy: 这种内部写法——把存储格式漏给了对方。
    """
    await _seed()
    await _enable_emoji_pack()
    plugin = await _make_plugin()
    try:
        assert await plugin._emoji_to_chars("你好 [表情:害羞] 再见") == "你好  再见"
        assert await plugin._emoji_to_chars("你好 :shy: 再见") == "你好  再见", "已登记的图片短码不留"
        assert await plugin._emoji_to_chars("这个 [表情:没这个] 丢掉") == "这个  丢掉", "旧写法不留痕"
        assert await plugin._emoji_to_chars("正文里的 :没这个: 别动") == "正文里的 :没这个: 别动", \
            "未登记的短码可能是正文自带内容，保留"
        assert await plugin._emoji_to_chars("没有表情") == "没有表情"
    finally:
        _cleanup(plugin)


async def test_ai_message_quoted_in_qq_resolves_too(migrated_db):
    """用户引用的若是 AI 在 QQ 里发的那条：出站记下的 ext_info.ref_idx 同样对得上"""
    from app.chat.gm import send_gm_message
    from app.database import async_session

    await _seed()
    plugin = await _make_plugin()
    plugin._copree_group_id = GROUP_ID
    try:
        await plugin._on_group_at(dict(GROUP_EVENT))        # 先来一条入站，出站才知道回哪个群
        async with async_session() as db:
            ai_message = await send_gm_message(
                db, group_id=GROUP_ID, sender_type="ai", sender_id=AGENT_USER, content="晚安",
            )
            ai_id = int(ai_message.id)
            await db.commit()
        await _wait_sent(plugin)
        ref = await _wait_channel_ref(ai_id)
        assert ref, "出站没把通道侧的 ext_info.ref_idx 记回库里"

        await plugin._on_group_message(_quote_event(
            "Q-AI", content="什么意思", quoted="晚安", ref_idx=ref, my_idx="IDX-AI",
        ))

        rows = await _messages_with_quote(GROUP_ID)
        assert rows[-1][2] == ai_id, f"引用 AI 的消息也该对上：{rows[-1]}"
    finally:
        _cleanup(plugin)




def test_recall_period_boundaries():
    """召回周期就是官方那四段：当天 / 1-3 天 / 3-7 天 / 7-30 天，边界取上界"""
    _load_plugin_module()
    fmt = _plugin_module("format")
    assert [fmt.recall_period(d) for d in (0.0, 0.9, 1.0, 2.9, 3.0, 6.9, 7.0, 29.9)] == \
        [0, 0, 1, 1, 2, 2, 3, 3]
    assert fmt.recall_period(30.0) is None
    assert fmt.recall_period(999.0) is None


async def test_group_reply_after_window_fails_loudly(migrated_db):
    """群回复窗口过期：不发那条假的主动消息，并把原因写成一条系统通知给主人

    群聊没有主动推送（2025-04-21 起下线）、也没有召回字段，硬发只会换一个错误码；
    而人在 Copree 里只看到 AI 说得热闹，不知道该去 QQ 里再 @ 一次——所以要让他知道。
    """
    import time

    from app.database import async_session

    await _seed()
    plugin = await _make_plugin()
    try:
        route = {"copree_group_id": GROUP_ID, "qq": "QQGROUP-AAA", "msg_id": "MSG-1", "seq": 0,
                 "ts": time.time() - 3600, "peer_name": "小明", "peer_openid": "OPENID-XYZ"}
        plugin._routes["QQGROUP-AAA"] = route
        await plugin._send_reply(route, "晚了一步", kind="group")

        assert plugin._client.sent == [], "窗口过期后不该再往群里发"
        async with async_session() as db:
            notices = (await db.execute(text(
                "SELECT content FROM dm_messages WHERE sender_id = 0"
            ))).scalars().all()
        assert len(notices) == 1, notices
        assert "QQ 通道发不出消息" in notices[0], notices
        assert "没有主动/召回能力" in notices[0], notices
        assert plugin.last_error.startswith("发送失败："), plugin.last_error
    finally:
        _cleanup(plugin)


async def test_dm_after_window_spends_one_recall_per_period(migrated_db):
    """私聊窗口过期：改走互动召回（is_wakeup），同一个周期只花一条"""
    import time

    from app.database import async_session

    await _seed()
    plugin = await _make_plugin()
    try:
        route = {"session_id": "S1", "qq": "OPENID-DM", "msg_id": "DMMSG-1", "seq": 0,
                 "ts": time.time() - 7200}
        await plugin._send_reply(route, "在的", kind="dm")
        sent = plugin._client.sent[-1]
        assert sent["kind"] == "dm" and sent["wakeup"] is True, sent
        assert sent["msg_id"] is None, "召回消息与 msg_id 互斥，必须摘掉被动凭据"
        assert plugin.recalls == 1, plugin.recalls

        route["ts"] = time.time() - 7200          # 对方一直没再说话：还是同一个周期
        await plugin._send_reply(route, "还在吗", kind="dm")
        assert len(plugin._client.sent) == 1, "同一周期不该再花第二条召回额度"

        async with async_session() as db:
            row = (await db.execute(text(
                "SELECT used_mask FROM channel_wakeup_ledger WHERE target = 'OPENID-DM'"
            ))).first()
        assert row is not None and int(row[0]) == 0b1, row
    finally:
        _cleanup(plugin)


async def test_recall_cycle_reopens_after_thirty_days(migrated_db):
    """30 天期满后再说话：开一个新周期，四个名额重新计（账本跨重启还在）"""
    import time
    from datetime import datetime, timedelta, timezone

    from app.database import async_session
    from app.models.external import ChannelWakeupLedger

    await _seed()
    plugin = await _make_plugin()
    try:
        stale = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=31)
        async with async_session() as db:
            db.add(ChannelWakeupLedger(
                kind=plugin.channel_kind, owner_scope=plugin.instance, target="OPENID-DM",
                anchor_at=stale, used_mask=0b1111,
            ))
            await db.commit()

        # 对方 31 天后又说了一句：被动窗口之内回得到，等窗口过了才用得着新周期的召回额度
        route = {"session_id": "S1", "qq": "OPENID-DM", "msg_id": "DMMSG-1", "seq": 0,
                 "ts": time.time() - 7200}
        await plugin._send_reply(route, "好久不见", kind="dm")
        assert plugin._client.sent[-1]["wakeup"] is True, plugin._client.sent

        async with async_session() as db:
            row = (await db.execute(text(
                "SELECT used_mask FROM channel_wakeup_ledger WHERE target = 'OPENID-DM'"
            ))).first()
        assert int(row[0]) == 0b1, "新周期应该只剩刚花掉的那一个名额"
    finally:
        _cleanup(plugin)

async def test_merged_group_replies_only_through_the_owning_bot(migrated_db):
    """群聊合并：一个 Copree 群被两个机器人接着时，回复只由"来消息那个机器人"发出

    被动凭据只活在各自进程里，谁都不知道别人见过哪条消息。靠内存索引时，
    没见过的那个实例会退回"本群最近来消息的那条"，把 B 群的消息回进 A 群。
    会话标识记在消息行上之后，另一个实例认得出"这条不归我"。
    """
    import time

    from app.chat.gm import send_gm_message
    from app.database import async_session

    from app.services.infrastructure.plugin_registry import PluginRegistry

    await _seed()
    bot_a = await _make_plugin("bot-a")
    bot_b = await _make_plugin("bot-b")
    # 真实启动过的实例都在注册表里："作者归谁"要问它，没登记的兄弟看不见（见 _carrier）
    PluginRegistry.register(bot_a)
    PluginRegistry.register(bot_b)
    try:
        # 两个机器人都接同一个 Copree 群（这就是"合并"）；A 群里刚有人说过话
        bot_a._routes["QQGROUP-A"] = {
            "qq": "QQGROUP-A", "copree_group_id": GROUP_ID, "msg_id": "MSG-A", "seq": 0,
            "ts": time.time(), "peer_name": "小明", "peer_openid": "OPENID-A",
        }
        assert bot_a._route_for_outbound(GROUP_ID, "") is not None,             "A 有可用凭据：这正是旧逻辑会拿来代发的那条"
        await bot_b._on_group_at(dict(GROUP_EVENT, id="MSG-B", group_openid="QQGROUP-B"))

        async with async_session() as db:
            inbound = (await db.execute(text(
                "SELECT id FROM messages WHERE group_id = :g ORDER BY id DESC LIMIT 1"
            ), {"g": GROUP_ID})).scalar()
            assert inbound, "QQ 消息没有落库"
            assert (await db.execute(text(
                "SELECT channel_origin FROM messages WHERE id = :i"
            ), {"i": inbound})).scalar() == "qq:bot-b:QQGROUP-B", "落库没带上会话标识"
            await send_gm_message(db, GROUP_ID, "ai", AGENT_USER, "收到", reply_to=inbound)
            await db.commit()
        await _wait_sent(bot_b)

        assert [s["target"] for s in bot_b._client.sent] == ["QQGROUP-B"], bot_b._client.sent
        assert bot_a._client.sent == [], f"不该由另一个机器人代发：{bot_a._client.sent}"
    finally:
        PluginRegistry.unregister(bot_a.key)
        PluginRegistry.unregister(bot_b.key)
        _cleanup(bot_a)
        _cleanup(bot_b)


async def test_union_openid_binds_one_account_across_bots(migrated_db):
    """两台机器人认到同一个 union → 一个本地账号；两边的地址各留一份（@ 他要用各自那份）"""
    from app.database import async_session
    from app.services.channel import identity
    from app.services.channel.anchor import ensure_channel_user

    await _seed()
    async with async_session() as db:
        first = await ensure_channel_user(
            db, kind="qq", owner_scope="bot-a", origin="OPENID-A", display_name="小明",
            origin_channel="qq", union_id="UNION-X", commit=False,
        )
        second = await ensure_channel_user(
            db, kind="qq", owner_scope="bot-b", origin="OPENID-B", display_name="小明",
            origin_channel="qq", union_id="UNION-X", commit=False,
        )
        await db.commit()
    assert first[0] == second[0], f"同一个 union 应该只占一个账号：{first} {second}"

    async with async_session() as db:
        assert await identity.contacts(db, kind="qq", owner_scope="bot-a", user_ids=[first[0]]) == {
            first[0]: "OPENID-A",
        }
        assert await identity.contacts(db, kind="qq", owner_scope="bot-b", user_ids=[first[0]]) == {
            first[0]: "OPENID-B",
        }


async def test_same_qq_message_from_two_bots_lands_once(migrated_db):
    """两台机器人都收到了同一句 QQ 消息：站内只留一条，两侧的人是同一个账号

    合并后的群里同一句话出现两遍、说话人还是两个账号——这是"把两台机器人都拉进一个群"
    最先撞上的事（两台都开了全量模式时，群里每条人话都会各送一次）。
    """
    from app.database import async_session

    await _seed()
    bot_a = await _make_plugin("bot-a")
    bot_b = await _make_plugin("bot-b")
    try:
        await bot_a._on_group_at(dict(GROUP_EVENT, id="MSG-A", group_openid="QQGROUP-A"))
        await bot_b._on_group_at(dict(GROUP_EVENT, id="MSG-B", group_openid="QQGROUP-B"))

        async with async_session() as db:
            rows = (await db.execute(text(
                "SELECT id, sender_id, channel_origin FROM messages WHERE group_id = :g"
            ), {"g": GROUP_ID})).all()
            users = (await db.execute(text(
                "SELECT owner_scope, origin, user_id FROM external_identities WHERE kind = 'qq'"
            ))).all()
        assert len(rows) == 1, f"同一句只该落一条：{rows}"
        assert rows[0][2] == "qq:bot-a:QQGROUP-A", rows
        assert {u[0] for u in users} == {"bot-a", "bot-b"}, users
        assert len({u[2] for u in users}) == 1, f"两侧应该是同一个账号：{users}"
    finally:
        _cleanup(bot_a)
        _cleanup(bot_b)

async def test_same_qq_message_with_mention_lands_once_across_two_bots(migrated_db):
    """带 @ 的同一句也要认出亲：两台是**不同的 AI**，各补各的唤醒令牌、各自解析被 @ 的成员。

    逐字比会比不出来（旧做法就是把渲染后的字节拿去 SQL 等值比），于是合并后的群里同一句话
    落两遍。认亲要比的是"人说的那句话"，所以先摘令牌再比（见 _same_channel_text）。
    fixture 里两台同名同 id 恰好掩盖了这个洞，这条特意让它们不同。
    """
    from app.database import async_session

    await _seed()
    bot_a = await _make_plugin("bot-a")
    bot_b = await _make_plugin("bot-b")
    bot_b._target_agent = "绵绵"
    bot_b._target_user_id = AGENT_USER + 1
    try:
        event = dict(
            GROUP_EVENT,
            content="你看这个 <@OPENID-NEW> 对吧",
            mentions=[{"id": "OPENID-NEW", "username": "小红"}],
        )
        await bot_a._on_group_at(dict(event, id="MEN-A", group_openid="QQGROUP-A"))
        await bot_b._on_group_at(dict(event, id="MEN-B", group_openid="QQGROUP-B"))

        async with async_session() as db:
            rows = (await db.execute(text(
                "SELECT id, content FROM messages WHERE group_id = :g"
            ), {"g": GROUP_ID})).all()
        assert len(rows) == 1, f"带 @ 的同一句也只该落一条：{rows}"
    finally:
        _cleanup(bot_a)
        _cleanup(bot_b)

async def test_short_message_from_two_bots_lands_once(migrated_db):
    """单字（"！"）也要认亲：短句没有令牌可比，改按「同一个人 + 同一句」判。

    几台机器人接同一个群，同一条 QQ 消息每台都会收到；太短的正文以前直接不认，
    于是一个人发一个"！"群里就多出四条。
    """
    from app.database import async_session

    await _seed()
    bot_a = await _make_plugin("bot-a")
    bot_b = await _make_plugin("bot-b")
    bot_b._target_agent = "绵绵"
    bot_b._target_user_id = AGENT_USER + 1
    try:
        event = dict(GROUP_EVENT, content="！")
        await bot_a._on_group_at(dict(event, id="SHORT-A", group_openid="QQGROUP-A"))
        await bot_b._on_group_at(dict(event, id="SHORT-B", group_openid="QQGROUP-B"))
        async with async_session() as db:
            rows = (await db.execute(text(
                "SELECT id, content FROM messages WHERE group_id = :g"
            ), {"g": GROUP_ID})).all()
        assert len(rows) == 1, f"同一个人的同一句短消息只该落一条：{rows}"
    finally:
        _cleanup(bot_a)
        _cleanup(bot_b)


async def test_one_bot_sends_when_two_serve_the_same_group(migrated_db):
    """两台机器人接同一个 Copree 群：AI 自己起的话头（没有来源可认）只由一台发出去

    来源可认的回复已经由 channel_origin 定死了归谁；这条管的是认不出来的时候——
    两台各自"本群最近来消息那条"都有凭据，谁也不让，群里就会出现两条一样的消息。
    """
    import time

    from app.chat.gm import send_gm_message
    from app.database import async_session
    from app.services.infrastructure.plugin_registry import PluginRegistry

    await _seed()
    bot_a = await _make_plugin("bot-a")
    bot_b = await _make_plugin("bot-b")
    PluginRegistry.register(bot_a)
    PluginRegistry.register(bot_b)
    try:
        now = time.time()
        for bot, openid in ((bot_a, "QQGROUP-A"), (bot_b, "QQGROUP-B")):
            assert bot._serves_group(GROUP_ID)
            bot._routes[openid] = {
                "qq": openid, "copree_group_id": GROUP_ID, "msg_id": f"MSG-{openid}", "seq": 0,
                "ts": now, "peer_name": "小明", "peer_openid": f"OPENID-{openid}",
            }
        async with async_session() as db:
            await send_gm_message(db, GROUP_ID, "ai", AGENT_USER, "我先说一句")
            await db.commit()
        await _wait_sent(bot_a)

        assert [s["target"] for s in bot_a._client.sent] == ["QQGROUP-A"], bot_a._client.sent
        assert bot_b._client.sent == [], f"第二台不该再发一遍：{bot_b._client.sent}"
    finally:
        PluginRegistry.unregister(bot_a.key)
        PluginRegistry.unregister(bot_b.key)
        _cleanup(bot_a)
        _cleanup(bot_b)

async def test_each_bot_sends_only_its_own_ais_words(migrated_db):
    """作者归属优先于会话归属：别人的 AI 说的话，不能挂我的机器人名发出去

    合并群里那条人话是 A 落库的（会话归 A），可它回的是 B 的 AI——旧逻辑照会话判，
    于是 B 的 AI 说的话由 A 的机器人发进 QQ 群（2026-10-05 真机：一台机器人说的话，
    挂着另一台的名进了群）。
    """
    import time

    from app.chat.gm import send_gm_message
    from app.database import async_session
    from app.services.infrastructure.plugin_registry import PluginRegistry

    await _seed()
    bot_a = await _make_plugin("bot-a")
    bot_b = await _make_plugin("bot-b")
    bot_b._target_agent = "小蓝"
    bot_b._target_user_id = 3
    PluginRegistry.register(bot_a)
    PluginRegistry.register(bot_b)
    try:
        async with async_session() as db:
            await db.execute(text(
                "INSERT INTO users (id, username, password_hash, type) VALUES (3, '小蓝', 'x', 'ai')"
            ))
            await db.execute(text(
                "INSERT INTO group_members (group_id, member_type, member_id, role) "
                f"VALUES ({GROUP_ID}, 'ai', 3, 'member')"
            ))
            await db.commit()
        now = time.time()
        for bot, openid in ((bot_a, "QQGROUP-A"), (bot_b, "QQGROUP-B")):
            bot._routes[openid] = {
                "qq": openid, "copree_group_id": GROUP_ID, "msg_id": f"MSG-{openid}", "seq": 0,
                "ts": now, "peer_name": "小明", "peer_openid": f"OPENID-{openid}",
            }
        await bot_a._on_group_at(dict(GROUP_EVENT, id="MSG-A", group_openid="QQGROUP-A"))
        async with async_session() as db:
            inbound = (await db.execute(text(
                "SELECT id FROM messages WHERE group_id = :g ORDER BY id DESC LIMIT 1"
            ), {"g": GROUP_ID})).scalar()
            await send_gm_message(db, GROUP_ID, "ai", 3, "小蓝说的话", reply_to=inbound)
            await db.commit()
        await _wait_sent(bot_b)

        assert [s["target"] for s in bot_b._client.sent] == ["QQGROUP-B"], bot_b._client.sent
        assert bot_a._client.sent == [], f"别人 AI 的话不该由我代发：{bot_a._client.sent}"
    finally:
        PluginRegistry.unregister(bot_a.key)
        PluginRegistry.unregister(bot_b.key)
        _cleanup(bot_a)
        _cleanup(bot_b)


async def test_unreachable_notices_back_off_exponentially(migrated_db):
    """发不出去的站内提示：同一个目标按次数指数回避（5 分钟起，最高 5 天），发出去了就清零

    失败要看得见，但一直失败时不能一直敲人：第二次提示起翻倍等待，封顶 5 天一条。
    """
    import time

    await _seed()
    module = _load_plugin_module()
    plugin = await _make_plugin()
    sent: list[str] = []

    async def _capture(db, text):
        sent.append(text)

    plugin._notify_owner = _capture
    route = {"qq": "QQGROUP-AAA", "copree_group_id": GROUP_ID, "msg_id": "MSG-1", "seq": 0}
    key = plugin._notice_key("group", route)
    common = _plugin_module("common")
    base, cap = common.UNREACHABLE_NOTICE_BASE, common.UNREACHABLE_NOTICE_MAX
    try:
        await plugin._report_unreachable("group", route, "这个群还没有被动回复凭据")
        await plugin._report_unreachable("group", route, "这个群还没有被动回复凭据")
        assert len(sent) == 1, f"紧接着再来一次不该再提示：{sent}"

        # 已经提示过 4 次 → 这一次要等 base * 2^4 秒；差 5 秒不发，过了才发
        plugin._fail_notice[key] = (4, time.time() - base * 16 + 5)
        await plugin._report_unreachable("group", route, "还没凭据")
        assert len(sent) == 1, "没到点不该提示"
        plugin._fail_notice[key] = (4, time.time() - base * 16 - 5)
        await plugin._report_unreachable("group", route, "还没凭据")
        assert len(sent) == 2, "到点该提示"
        assert plugin._fail_notice[key][0] == 5, plugin._fail_notice[key]

        # 次数再多也不会比 5 天更稀
        plugin._fail_notice[key] = (60, time.time() - cap + 5)
        await plugin._report_unreachable("group", route, "还没凭据")
        assert len(sent) == 2, "封顶不到就不提示"
        plugin._fail_notice[key] = (60, time.time() - cap - 5)
        await plugin._report_unreachable("group", route, "还没凭据")
        assert len(sent) == 3, "过了封顶间隔该提示"

        # 这条发成功了 → 这个目标的回避清零，下次从 5 分钟重新起算
        route_out = {"qq": "QQGROUP-AAA", "copree_group_id": GROUP_ID, "msg_id": "MSG-1",
                     "seq": 0, "ts": time.time()}
        await plugin._send_reply(route_out, "好了", "group")
        assert key not in plugin._fail_notice, plugin._fail_notice
    finally:
        _cleanup(plugin)


def test_split_message_prefers_paragraph_breaks():
    """长文按段拆：优先空行、其次单行，都不够长才硬切，而且每段都不超限"""
    _load_plugin_module()
    fmt = _plugin_module("format")
    text = "\n\n".join(["甲" * 40, "乙" * 40, "丙" * 40])
    pieces = fmt.split_message(text, 50)
    assert len(pieces) == 3, pieces
    assert all(len(p) <= 50 for p in pieces), pieces
    assert pieces[0] == "甲" * 40, pieces[0]

    long_line = "丁" * 130                      # 没有换行可断 → 硬切
    pieces = fmt.split_message(long_line, 50)
    assert [len(p) for p in pieces] == [50, 50, 30], pieces
    assert fmt.split_message("", 50) == []


async def test_long_reply_is_split_not_truncated(migrated_db):
    """整条先发被平台拒后二分重发：拆成两条也不许砍掉后半段（旧代码是 content[:1000] 直接截断）"""
    import time

    await _seed()
    plugin = await _make_plugin()
    try:
        route = {"copree_group_id": GROUP_ID, "qq": "QQGROUP-AAA", "msg_id": "MSG-1", "seq": 0,
                 "ts": time.time(), "peer_name": "小明", "peer_openid": "OPENID-XYZ"}
        plugin._routes["QQGROUP-AAA"] = route
        plugin._client.length_limit = 3000
        await plugin._send_reply(route, "字" * 6000, kind="group")

        sent = plugin._client.sent
        assert [len(s["content"]) for s in sent] == [3000, 3000], sent
        assert [s["seq"] for s in sent] == [2, 3], f"被拒的那次也要占号：{sent}"
        assert all(s["msg_id"] == "MSG-1" for s in sent), sent
        assert "".join(s["content"] for s in sent).count("字") == 6000, "拆开也不许丢正文"
        assert plugin._max_chars == 3000, plugin._max_chars
        assert route["seq"] == 3, route
    finally:
        _cleanup(plugin)


async def test_union_is_backfilled_once_and_cached(migrated_db):
    """事件里没有 union 时按需补拉一次并缓存；接口没权限（11253）就不再撞第二次"""
    module = _load_plugin_module()

    await _seed()
    plugin = await _make_plugin()
    try:
        author: dict = {"member_openid": "OPENID-XYZ", "username": "小明"}
        await plugin._fill_union("QQGROUP-AAA", author)
        assert author["union_openid"] == "UNION-FROM-API", author
        assert len(plugin._client.member_calls) == 1, plugin._client.member_calls

        other: dict = {"member_openid": "OPENID-XYZ", "username": "小明"}     # 同一个人再来一条
        await plugin._fill_union("QQGROUP-AAA", other)
        assert other["union_openid"] == "UNION-FROM-API", other
        assert len(plugin._client.member_calls) == 1, "命中缓存后不该再问接口"

        plugin._client.member_error = "取群成员信息失败（HTTP 403）：{'code': 11253}"
        fresh: dict = {"member_openid": "OPENID-OTHER", "username": "小红"}
        await plugin._fill_union("QQGROUP-AAA", fresh)
        assert plugin._member_info_denied is True
        assert len(plugin._client.member_calls) == 2, plugin._client.member_calls
        await plugin._fill_union("QQGROUP-AAA", {"member_openid": "OPENID-THIRD"})
        assert len(plugin._client.member_calls) == 2, "已确认无权限就不该再撞"
    finally:
        _cleanup(plugin)


def test_activity_indicator_keeps_one_id_space():
    """活动指示器的键只能有一把尺（user_id）：一处按 agent.id 存、一处按 user_id 发，
    同一个 AI 就会同时占两条（"某某、某某 等2人"），而且恢复出来的那条永远清不掉。"""
    src = (PLUGIN_PATH.parents[2] / "app" / "ai" / "response_worker.py").read_text()
    assert "_thinking_state.setdefault(conv_key, {})[agent.user_id]" in src
    assert "_thinking_state.setdefault(conv_key, {})[agent_user_id]" in src
    assert '"user_id": agent_user_id' in src
    assert ".pop(agent.id, None)" not in src

async def test_overlong_reply_is_resplit_and_the_limit_is_learned(migrated_db):
    """被拒之后二分，两侧边界都记下来；下一条比被拒的长度短就直接整条发，不再撞同一个长度

    单条到底能多长，官方只给了错误码、没给数字，所以不猜死：整条先发、被拒就学。
    被拒的那一次也占了序号（同一个 msg_id+seq 重发会被判"消息被去重"）。
    """
    import time

    await _seed()
    plugin = await _make_plugin()
    try:
        route = {"copree_group_id": GROUP_ID, "qq": "QQGROUP-AAA", "msg_id": "MSG-1", "seq": 0,
                 "ts": time.time(), "peer_name": "小明", "peer_openid": "OPENID-XYZ"}
        plugin._routes["QQGROUP-AAA"] = route
        plugin._client.length_limit = 1200
        await plugin._send_reply(route, "字" * 1500, kind="group")

        sent = plugin._client.sent
        assert [len(s["content"]) for s in sent] == [750, 750], sent
        assert "".join(s["content"] for s in sent).count("字") == 1500, "拆开也不许丢正文"
        assert [s["seq"] for s in sent] == [2, 3], f"被拒的那次也要占号：{sent}"
        assert plugin._max_chars == 750, plugin._max_chars
        assert plugin._too_long == 1500, plugin._too_long
        assert route["seq"] == 3, route

        # 下一条 1000 字：比被拒的 1500 短，直接整条发
        route["seq"] = 0
        plugin._client.sent.clear()
        await plugin._send_reply(route, "字" * 1000, kind="group")
        assert [len(s["content"]) for s in plugin._client.sent] == [1000], plugin._client.sent
    finally:
        _cleanup(plugin)


async def test_reply_tries_the_whole_message_first(migrated_db):
    """"认过的长度"不是上限：下一条更长也整条先发，能过就不用拆"""
    import time

    await _seed()
    plugin = await _make_plugin()
    try:
        route = {"copree_group_id": GROUP_ID, "qq": "QQGROUP-AAA", "msg_id": "MSG-1", "seq": 0,
                 "ts": time.time(), "peer_name": "小明", "peer_openid": "OPENID-XYZ"}
        plugin._routes["QQGROUP-AAA"] = route
        plugin._client.length_limit = 5000
        await plugin._send_reply(route, "字" * 5000, kind="group")
        assert [len(s["content"]) for s in plugin._client.sent] == [5000], plugin._client.sent
        assert plugin._max_chars == 5000, plugin._max_chars

        route["seq"] = 0
        plugin._client.sent.clear()
        plugin._client.length_limit = 6000
        await plugin._send_reply(route, "字" * 6000, kind="group")
        assert [len(s["content"]) for s in plugin._client.sent] == [6000], plugin._client.sent
        assert plugin._max_chars == 6000, plugin._max_chars
    finally:
        _cleanup(plugin)


async def test_reply_bisects_between_known_good_and_rejected(migrated_db):
    """二分：5000 被拒（真实上限 4200）→ 砍半 2500 发出去；下一条 4500 再被拒 → 取中点 3500"""
    import time

    await _seed()
    plugin = await _make_plugin()
    try:
        route = {"copree_group_id": GROUP_ID, "qq": "QQGROUP-AAA", "msg_id": "MSG-1", "seq": 0,
                 "ts": time.time(), "peer_name": "小明", "peer_openid": "OPENID-XYZ"}
        plugin._routes["QQGROUP-AAA"] = route
        plugin._client.length_limit = 4200
        await plugin._send_reply(route, "字" * 5000, kind="group")

        sent = plugin._client.sent
        assert [len(s["content"]) for s in sent] == [2500, 2500], sent
        assert "".join(s["content"] for s in sent).count("字") == 5000, "拆开也不许丢正文"
        assert plugin._max_chars == 2500, plugin._max_chars
        assert plugin._too_long == 5000, plugin._too_long

        # 下一条 4500：先整条（差一点），被拒后取"认过的 2500"和"被拒的 4500"的中点
        route["seq"] = 0
        plugin._client.sent.clear()
        await plugin._send_reply(route, "字" * 4500, kind="group")
        assert [len(s["content"]) for s in plugin._client.sent] == [3500, 1000], plugin._client.sent
        assert plugin._max_chars == 3500, plugin._max_chars
    finally:
        _cleanup(plugin)


async def test_first_size_has_no_artificial_ceiling(migrated_db):
    """没被拒过就整条试；拒过之后也只按那次实测的上界收着，不认任何写死的天花板"""
    await _seed()
    plugin = await _make_plugin()
    try:
        assert plugin._first_size(50000) == 50000
        plugin._too_long = 20000
        assert plugin._first_size(50000) == 19999
        assert plugin._first_size(3000) == 3000
    finally:
        _cleanup(plugin)
async def test_route_is_remembered_even_when_delivery_fails(migrated_db):
    """落库失败也不能让这台机器人丢了被动凭据：凭据是"腾讯刚推来这条"，从收到那刻就成立

    2026-10-05 真机：锚点账号改名撞唯一约束 → 整条入站失败 → 路由没登记 → AI 的回话被判
    "没有凭据"跳过。落点能从内存映射算出来就该先记上，投递完再覆写补 peer 信息。
    """
    await _seed()
    plugin = await _make_plugin()
    try:
        async def _boom(*a, **k):
            raise RuntimeError("落库炸了")

        plugin._deliver_to_group = _boom
        await plugin._on_group_message({**GROUP_EVENT, "id": "ROUTE-1", "group_openid": "QQGROUP-AAA"})

        route = plugin._routes.get("QQGROUP-AAA")
        assert route is not None, "收到消息就该有凭据（落库失败也一样）"
        assert int(route["copree_group_id"]) == GROUP_ID, route
        assert route["msg_id"] == "ROUTE-1", route
    finally:
        _cleanup(plugin)


async def test_identity_stays_bound_after_the_twin_merge(migrated_db):
    """认亲定下的账号不能因为下一条消息又分家

    两台机器人各看到一个 openid：同一句先被认亲接到先来的账号上，之后这一侧单独说一句
    （没有第二台收到，认亲无从触发）也必须还是那个账号——以前的代码在这里按 openid 找回
    自己的锚点，于是同一个 AN 成了两个账号（2026-10-18 真机：群 #77）。
    """
    from app.database import async_session

    await _seed()
    bot_a = await _make_plugin("bot-a")
    bot_b = await _make_plugin("bot-b")
    # 不补拉 union：这条测的是"认亲（同一句话）之后绑定还在"，不是 union 认人
    bot_a._client.member_union = bot_b._client.member_union = ""
    try:
        await bot_a._on_group_message(dict(
            GROUP_EVENT, id="BIND-A1", content="今天天气不错", mentions=[], group_openid="QQGROUP-A",
        ))
        await bot_b._on_group_message(dict(
            GROUP_EVENT, id="BIND-B1", content="今天天气不错", mentions=[], group_openid="QQGROUP-B",
            author={"member_openid": "OPENID-B", "username": "小明"},
        ))
        # 这一侧单独说一句：认亲不参与
        await bot_b._on_group_message(dict(
            GROUP_EVENT, id="BIND-B2", content="只有我这一侧收到的一句", mentions=[],
            group_openid="QQGROUP-B", author={"member_openid": "OPENID-B", "username": "小明"},
        ))

        async with async_session() as db:
            rows = (await db.execute(text(
                "SELECT sender_id FROM messages WHERE group_id = :g ORDER BY id"
            ), {"g": GROUP_ID})).scalars().all()
            anchors = (await db.execute(text(
                "SELECT id FROM users WHERE email LIKE '%@qq.bridge' ORDER BY id"
            ))).scalars().all()
        assert len(rows) == 2, rows
        assert len(set(rows)) == 1, f"同一句、同一个人的消息该是同一个账号：{rows}"
        assert len(anchors) == 1, f"只该有一个通道锚点账号：{anchors}"
    finally:
        _cleanup(bot_a)
        _cleanup(bot_b)


async def test_twin_adoption_merges_the_later_account_into_the_earlier_one(migrated_db):
    """两边都建过账号之后认出同一句：并进**早先**那个，不能留后建的空壳当正主

    2026-10-18 真机：AN 先在一台机器人下落了号，另一台机器人第一次见到他时，同一句话先到的
    那侧顺手建了个 AN#2。认亲只把 identity 改过去、账号本身没并 —— 群里于是多出一个 AN，
    AI 一人一半地跟他说话。
    """
    from app.database import async_session

    await _seed()
    bot_a = await _make_plugin("bot-a")
    bot_b = await _make_plugin("bot-b")
    # 不补拉 union：两边各建一个账号，靠同一句话认亲把它们并起来
    bot_a._client.member_union = bot_b._client.member_union = ""
    try:
        await bot_a._on_group_message(dict(
            GROUP_EVENT, id="MERGE-A1", content="我这边先说一句", mentions=[], group_openid="QQGROUP-A",
        ))
        # 另一台机器人第一次见到这个人：先自己建了一个账号（还没有认亲的机会）
        await bot_b._on_group_message(dict(
            GROUP_EVENT, id="MERGE-B1", content="我这一侧第一次说话", mentions=[],
            group_openid="QQGROUP-B", author={"member_openid": "OPENID-B", "username": "小明"},
        ))
        async with async_session() as db:
            before = (await db.execute(text(
                "SELECT id FROM users WHERE email LIKE '%@qq.bridge' ORDER BY id"
            ))).scalars().all()
        assert len(before) == 2, f"两边各建过一个账号：{before}"
        earlier = int(before[0])

        # 同一句话两台机器人各收到一次：后到的那侧认亲 → 并进早先那个账号
        await bot_a._on_group_message(dict(
            GROUP_EVENT, id="MERGE-A2", content="同一句话", mentions=[], group_openid="QQGROUP-A",
        ))
        await bot_b._on_group_message(dict(
            GROUP_EVENT, id="MERGE-B2", content="同一句话", mentions=[], group_openid="QQGROUP-B",
            author={"member_openid": "OPENID-B", "username": "小明"},
        ))

        async with async_session() as db:
            rows = (await db.execute(text(
                "SELECT sender_id FROM messages WHERE group_id = :g ORDER BY id"
            ), {"g": GROUP_ID})).scalars().all()
            left = (await db.execute(text(
                "SELECT id FROM users WHERE email LIKE '%@qq.bridge' ORDER BY id"
            ))).scalars().all()
            members = (await db.execute(text(
                "SELECT member_id FROM group_members WHERE group_id = :g AND member_type = 'human'"
            ), {"g": GROUP_ID})).scalars().all()
            identities = (await db.execute(text(
                "SELECT user_id FROM external_identities WHERE kind = 'qq'"
            ))).scalars().all()
        assert len(rows) == 3, f"同一句只该落一条：{rows}"
        assert set(rows) == {earlier}, f"都该并到早先那个账号：{rows} vs {earlier}"
        assert left == [earlier], f"后建的空壳该被并掉：{left}"
        assert set(members) - {GROUP_OWNER} == {earlier}, f"群里只该有他一条成员记录：{members}"
        assert set(identities) == {earlier}, f"两个地址都该指向同一个账号：{identities}"
    finally:
        _cleanup(bot_a)
        _cleanup(bot_b)


async def test_gateway_session_change_reconnects_without_backoff():
    """op7/op9 换会话按"立刻重连"处理，不进退避。

    退避只在连接真出错时才有意义；换会话是 token 到期的例行动作，按故障退避会一路
    涨到 BACKOFF_MAX，于是每小时都留一段收不到消息的空窗（实测 60 秒）。
    """
    module = _load_plugin_module()
    channel = object.__new__(module.QqChannelPlugin)
    channel.instance = "agent-test"
    channel.connected = False
    channel.last_error = ""

    dropped = []

    class FakeClient:
        def forget_token(self):
            dropped.append(True)

    channel._client = FakeClient()
    channel._session_id, channel._seq = "SESSION-1", 9
    calls = []
    gateway = _gateway_module()
    faults = [gateway._InvalidSession(), gateway._GatewayRestart(), gateway._GatewayRestart()]

    async def fake_connect():
        calls.append(asyncio.get_running_loop().time())
        if faults:
            raise faults.pop(0)
        raise asyncio.CancelledError()

    async def run():
        channel._connect_once = fake_connect
        try:
            await module.QqChannelPlugin._supervise(channel)
        except asyncio.CancelledError:
            pass

    gateway = _gateway_module()
    original = gateway.RECONNECT_PAUSE
    gateway.RECONNECT_PAUSE = 0.01          # 不真等 1 秒；抖动按比例缩到 0~0.01
    try:
        await run()
    finally:
        gateway.RECONNECT_PAUSE = original

    assert len(calls) == 4, f"三次换会话各该重连一次：{len(calls)}"
    gaps = [b - a for a, b in zip(calls, calls[1:])]
    assert all(gap < 0.2 for gap in gaps), f"换会话不该越等越久：{gaps}"
    assert max(gaps) - min(gaps) < 0.05, f"停顿应当是平的，不能逐次翻倍：{gaps}"
    assert dropped == [True], f"会话失效只该丢一次 token：{dropped}"
    assert channel.connected is False
    # op9 之后旧会话不能再续，否则会拿着一个已失效的 session_id 反复被拒
    assert (channel._session_id, channel._seq) == ("", 0), "会话失效要清掉续接凭据"


async def test_reconnect_resumes_the_session_before_identify():
    """有会话就先 op6 续接，网关会把断线期间漏掉的事件补发一遍；没有才 op2 重新鉴权。"""
    module = _load_plugin_module()
    channel = object.__new__(module.QqChannelPlugin)
    channel.instance = "agent-test"

    class FakeClient:
        async def token(self):
            return "TOKEN"

    class FakeWs:
        def __init__(self):
            self.sent = []

        async def send(self, raw):
            self.sent.append(json.loads(raw))

    channel._client = FakeClient()

    resumed = FakeWs()
    channel._session_id, channel._seq = "SESSION-1", 7
    await module.QqChannelPlugin._open_session(channel, resumed)
    assert resumed.sent == [{
        "op": _gateway_module().OP_RESUME,
        "d": {"token": "QQBot TOKEN", "session_id": "SESSION-1", "seq": 7},
    }]

    fresh = FakeWs()
    channel._session_id = ""
    await module.QqChannelPlugin._open_session(channel, fresh)
    gateway = _gateway_module()
    assert fresh.sent[0]["op"] == gateway.OP_IDENTIFY, "没有会话时走重新鉴权"
    assert fresh.sent[0]["d"]["intents"] == gateway.INTENTS


async def test_closed_before_the_session_is_established_restarts_identify():
    """续接被静默拒绝（连上但没到 RESUMED）时不能拿着死会话空转：清会话、退避一次、下一轮重新鉴权。"""
    module = _load_plugin_module()
    channel = object.__new__(module.QqChannelPlugin)
    channel.instance = "agent-test"
    channel.connected = False
    channel.last_error = ""
    channel._client = None
    channel._session_id, channel._seq = "STALE-SESSION", 11

    calls = []

    async def fake_connect():
        calls.append(1)
        if len(calls) == 1:
            return False               # 连上但没建立会话
        raise asyncio.CancelledError()

    async def run():
        channel._connect_once = fake_connect
        try:
            await module.QqChannelPlugin._supervise(channel)
        except asyncio.CancelledError:
            pass

    gateway = _gateway_module()
    original = gateway.BACKOFF_START
    gateway.BACKOFF_START = 0.01
    try:
        await run()
    finally:
        gateway.BACKOFF_START = original

    assert len(calls) == 2, f"该重试一次：{len(calls)}"
    assert (channel._session_id, channel._seq) == ("", 0), "死会话要清掉，下一轮重新鉴权"


async def test_ready_remembers_the_session_for_resume():
    """READY 里的 session_id 是续接的唯一凭据：没记住就等于每次都重新鉴权、断线必丢消息。"""
    module = _load_plugin_module()
    channel = object.__new__(module.QqChannelPlugin)
    channel.instance = "agent-test"
    channel.bot_name = ""
    channel.connected = False
    channel._session_id = ""

    await module.QqChannelPlugin._on_event(
        channel, "READY", {"session_id": "SESSION-2", "user": {"username": "大肥鱼"}})

    assert channel._session_id == "SESSION-2"
    assert channel.connected is True
    assert channel.bot_name == "大肥鱼"


# ── 图片：入站落成站内附件，出站先文本后图片 ──────────────────────

def test_media_picks_only_pictures_that_have_a_url():
    """事件里只挑"带 url 的图片"：语音/文件这轮不做，正文里的占位是它们的兜底"""
    media = _plugin_module("media")
    assert media.is_sendable_image({"content_type": "image/png"})
    assert not media.is_sendable_image({"content_type": "image/svg+xml"}), "官方图片接口不收 svg"
    assert media.mime_of({"content_type": "image/jpeg; charset=binary"}) == "image/jpeg"
    assert media.mime_of({"name": "cat.GIF"}) == "image/gif", "站内附件那份描述不一定带 mime"

    picked = media.event_attachments({"attachments": [
        {"content_type": "image/jpeg", "url": "https://gchat.qpic.cn/a.jpg", "filename": "a.jpg"},
        {"content_type": "voice", "url": "https://multimedia.nt.qq.com.cn/v.silk"},
        {"content_type": "image/png"},                     # 没有 url：取不到
        "not-a-dict",
    ]})
    assert [a["filename"] for a in picked] == ["a.jpg"], picked


async def test_upload_media_is_reused_and_the_picture_message_is_type_7():
    """图片消息（msg_type=7）：与 markdown 无关，纯文本模式的号也发得出去

    同一份内容在同一个会话里只传一次——官方给的 file_info 带 ttl，重复上传纯属浪费。
    """
    import tempfile

    module = _load_plugin_module()
    client = module.QqClient("app-id", "secret")
    calls: list[dict] = []

    async def fake_post(path, body, *, what="发 QQ 消息", timeout=None):
        calls.append({"path": path, "body": body, "what": what})
        if path.endswith("/files"):
            return {"file_info": "FILE-INFO", "file_uuid": "u-1", "ttl": 3600}
        return {"id": "m-1"}

    client._post = fake_post
    workdir = tempfile.mkdtemp(prefix="qq-media-")
    picture = os.path.join(workdir, "cat.png")
    with open(picture, "wb") as f:
        f.write(b"png-bytes" + b"0" * 32)

    assert await client.upload_media("group", "GROUP-1", picture, 1, "cat.png") == "FILE-INFO"
    upload = calls[0]
    assert upload["path"] == "/v2/groups/GROUP-1/files"
    assert upload["body"]["file_type"] == 1
    assert upload["body"]["srv_send_msg"] is False, "只上传：发不发、什么时候发由发送层定"
    assert upload["body"]["file_data"], "图片要 base64 带上去"
    assert upload["body"]["file_name"] == "cat.png"

    assert await client.upload_media("group", "GROUP-1", picture, 1, "cat.png") == "FILE-INFO"
    assert len(calls) == 1, f"同一份内容不该传第二次：{calls}"

    await client.send_media("group", "GROUP-1", "FILE-INFO", msg_id="M-1", msg_seq=2)
    sent = calls[-1]
    assert sent["path"] == "/v2/groups/GROUP-1/messages"
    assert sent["body"]["msg_type"] == 7
    assert sent["body"]["media"] == {"file_info": "FILE-INFO"}
    assert (sent["body"]["msg_id"], sent["body"]["msg_seq"]) == ("M-1", 2)


async def test_reply_sends_text_first_and_the_picture_after_it():
    """顺序按主人定的：先文本、再图片。图片同样占被动回复额度，所以 seq 顺延一位。

    这个运行器没有 pytest 的 monkeypatch/tmp_path（只认它自己的 fixture），
    所以临时目录自己建、改过的模块属性自己还原。
    """
    import tempfile

    from app.config import settings

    plugin = await _make_plugin()
    plugin._msg_type = 0                      # 正文固定成纯文本：图片照样要发得出去
    original_dir = settings.data_dir
    workdir = tempfile.mkdtemp(prefix="qq-media-")
    settings.data_dir = workdir
    os.makedirs(os.path.join(workdir, "attachments", "qq"))
    with open(os.path.join(workdir, "attachments", "qq", "cat.png"), "wb") as f:
        f.write(b"png-bytes" + b"0" * 32)
    route = {"qq": "GROUP-OPENID", "msg_id": "M-1", "seq": 0, "ts": time.time()}
    try:
        result = await plugin._deliver(
            route, "group", "看图",
            attachments=[{
                "path": "attachments/qq/cat.png", "name": "cat.png", "mime_type": "image/png",
            }],
        )
    finally:
        settings.data_dir = original_dir
        _cleanup(plugin)

    kinds = [item["kind"] for item in plugin._client.sent]
    assert kinds == ["group", "upload", "media"], kinds
    assert plugin._client.sent[0]["force_type"] == 0, "正文按纯文本发"
    assert plugin._client.sent[1]["file_type"] == 1
    assert plugin._client.sent[2]["seq"] == 2, "图片排在文本之后，占下一个 msg_seq"
    assert result["media_sent"] == 1 and result["media_skipped"] == 0


async def test_reply_sends_mp3_as_a_voice_message():
    """mp3 直接当语音消息发（file_type=3）：2026-11-14 私聊实测客户端能播，不转 silk。"""
    import tempfile

    from app.config import settings

    plugin = await _make_plugin()
    plugin._msg_type = 0                      # 正文固定纯文本：语音照样要发得出去
    original_dir = settings.data_dir
    workdir = tempfile.mkdtemp(prefix="qq-voice-")
    settings.data_dir = workdir
    os.makedirs(os.path.join(workdir, "attachments", "qq"))
    with open(os.path.join(workdir, "attachments", "qq", "hello.mp3"), "wb") as f:
        f.write(b"ID3" + b"0" * 64)
    route = {"qq": "GROUP-OPENID", "msg_id": "M-1", "seq": 0, "ts": time.time()}
    try:
        result = await plugin._deliver(
            route, "group", "听我说",
            attachments=[{
                "path": "attachments/qq/hello.mp3", "name": "hello.mp3",
                "mime_type": "audio/mpeg",
            }],
        )
    finally:
        settings.data_dir = original_dir
        _cleanup(plugin)

    assert [item["kind"] for item in plugin._client.sent] == ["group", "upload", "media"]
    assert plugin._client.sent[1]["file_type"] == 3, "mp3 走语音消息，不另外转码"
    assert plugin._client.sent[2]["seq"] == 2, "语音排在文本之后，占下一个 msg_seq"
    assert result["media_sent"] == 1 and result["media_skipped"] == 0


async def test_voice_message_rejected_falls_back_to_file():
    """语音发不出去时退成文件（file_type=4）：接口 200 不代表客户端会播，兜底得真发一次。"""
    import tempfile

    from app.config import settings

    plugin = await _make_plugin()
    plugin._msg_type = 0
    original_dir = settings.data_dir
    workdir = tempfile.mkdtemp(prefix="qq-voice-fallback-")
    settings.data_dir = workdir
    os.makedirs(os.path.join(workdir, "attachments", "qq"))
    with open(os.path.join(workdir, "attachments", "qq", "hello.mp3"), "wb") as f:
        f.write(b"ID3" + b"0" * 64)
    original = plugin._client.send_media
    tries = {"n": 0}

    async def flaky(kind, target, file_info, msg_id=None, msg_seq=1, wakeup=False):
        tries["n"] += 1
        if tries["n"] == 1:
            raise RuntimeError("客户端不收这条语音")
        return await original(kind, target, file_info, msg_id=msg_id, msg_seq=msg_seq,
                              wakeup=wakeup)

    plugin._client.send_media = flaky
    route = {"qq": "GROUP-OPENID", "msg_id": "M-1", "seq": 0, "ts": time.time()}
    try:
        result = await plugin._deliver(
            route, "group", "听我说",
            attachments=[{
                "path": "attachments/qq/hello.mp3", "name": "hello.mp3",
                "mime_type": "audio/mpeg",
            }],
        )
    finally:
        settings.data_dir = original_dir
        _cleanup(plugin)

    uploads = [item for item in plugin._client.sent if item["kind"] == "upload"]
    assert [u["file_type"] for u in uploads] == [3, 4], "先语音，失败再按文件传"
    assert result["media_sent"] == 1 and result["media_skipped"] == 0


async def test_inbound_picture_lands_as_an_attachment(migrated_db):
    """QQ 发来的图要落成站内附件：只留 [图片] 占位的话，AI 永远看不到图本身。"""
    import tempfile

    from app.config import settings

    original_dir = settings.data_dir
    workdir = tempfile.mkdtemp(prefix="qq-inbound-")

    async def fake_download(url):
        return b"png-bytes" + b"0" * 32, "image/png"

    await _seed()
    plugin = await _make_plugin()
    # 插件包是每次 _load_plugin_module() 重新组装的：不能按模块名 import 了再打补丁
    # （那样打到的是另一份模块对象），直接改这个方法自己看得见的那份全局表。
    store_globals = plugin._store_inbound_media.__func__.__globals__
    original_download = store_globals["download"]
    store_globals["download"] = fake_download
    settings.data_dir = workdir
    event = dict(GROUP_EVENT, attachments=[{
        "content_type": "image/png",
        "url": "https://gchat.qpic.cn/cat.png",
        "filename": "cat.png",
    }])
    try:
        await plugin._on_group_at(event)
        rows = await _messages(GROUP_ID)
    finally:
        store_globals["download"] = original_download
        settings.data_dir = original_dir
        _cleanup(plugin)

    assert len(rows) == 1, rows
    from app.database import async_session
    async with async_session() as db:
        stored = (await db.execute(text(
            "SELECT attachments FROM messages WHERE group_id = :g"
        ), {"g": GROUP_ID})).scalar()
    assert stored, "图片没落成附件，AI 那边只剩 [图片] 占位"
    assert stored[0]["mime_type"] == "image/png", stored
    assert stored[0]["path"].startswith("attachments/qq/"), stored
    assert os.path.isfile(os.path.join(workdir, stored[0]["path"])), "物理文件没落地"


async def test_send_failure_is_written_into_the_ai_ledger(migrated_db):
    """发不出去不能只让主人知道：AI 的上下文只有账本，它得看到"这句没到"才不会当作已发"""
    await _seed()
    plugin = await _make_plugin()
    try:
        await plugin._report_unreachable(
            "group", {"copree_group_id": GROUP_ID, "qq": "UP-AAA"}, "被动回复窗口已过",
        )
        entries = await _ledger(7)
    finally:
        _cleanup(plugin)

    notes = [e for e in entries if e.get("kind") == "note" and e.get("actor") == "system"]
    assert notes, entries
    assert any("没有送达" in str(e.get("content") or "") and "被动回复窗口已过" in str(e.get("content") or "")
               for e in notes), notes


async def test_picture_that_did_not_go_out_is_told_to_the_ai(migrated_db):
    """文字到了、图没到：AI 要收到的是"图没送到"，不是"整条没发出去" """
    await _seed()
    plugin = await _make_plugin()
    route = {"qq": "GROUP-OPENID", "copree_group_id": GROUP_ID,
             "msg_id": "M-1", "seq": 0, "ts": time.time()}
    try:
        # 附件指向一个不存在的文件：上传这一步就失败，文字那条仍算发出去了
        await plugin._post_reply(route, "group", "看图", "", None, [{
            "path": "attachments/qq/missing.png", "name": "missing.png", "mime_type": "image/png",
        }])
        entries = await _ledger(7)
    finally:
        _cleanup(plugin)

    assert [item["kind"] for item in plugin._client.sent] == ["group"], plugin._client.sent
    notes = [e for e in entries if e.get("kind") == "note"]
    assert any("张图没能送到 QQ" in str(e.get("content") or "") for e in notes), notes


