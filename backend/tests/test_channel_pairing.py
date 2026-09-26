"""我的 AI · 通道：归属、实例命名、配对（pairing）与配置护栏

四层：归属（不是你的 AI 就看不到也改不了）→ 配对状态流转（领码/批准/拉黑/解除）→
保存配置（目标 AI 由路径决定、不由用户指定；机密不回显）→ 管理台护栏（重扫配置不误删用户实例）。

用例在临时目录里造一个同名的假 QQ 插件并临时替换扫描根：真插件会去连腾讯，
而我们这里只验"通道这套编排"，不验网络。
"""
import json
import shutil
import tempfile
from pathlib import Path

from sqlalchemy import select, text

FAKE_PLUGIN = '''
from app.services.plugin.api import ServicePlugin, service


@service(
    name="QQ 通道（测试假件）",
    description="不联网，只验通道编排",
    multi_instance=True,
    config_schema={
        "app_id": {"type": "string", "title": "AppID", "required": True},
        "client_secret": {"type": "string", "title": "Secret", "secret": True, "required": True},
        "target_agent": {"type": "string", "title": "AI", "required": True},
        "copree_group_id": {"type": "string", "title": "接入的 Copree 群"},
        "qq_group_allowlist": {"type": "string", "title": "QQ 群白名单"},
        "dm_policy": {"type": "string", "title": "私聊策略"},
    },
)
class FakeQq(ServicePlugin):
    self_testable = True

    def __init__(self):
        self._running = False

    async def self_test(self):
        # 自测就是"真发一条"，所以假件把要发的内容原样回给调用方
        return {"sent": True, "target": "group", "text": "（通道自测，请忽略）"}

    async def get_status(self):
        return {"installed": True, "running": self._running}

    async def start(self):
        cfg = await self.config()
        if not cfg.get("app_id"):
            self.last_error = "未配置 AppID"
            return False
        self._running = True
        return True

    async def stop(self):
        self._running = False
        return True
'''


class _FakePluginDir:
    def __init__(self):
        self.root = Path(tempfile.mkdtemp(prefix="channel-test-"))
        d = self.root / "qq-channel"
        d.mkdir(parents=True)
        (d / "plugin.json").write_text(json.dumps({
            "id": "qq-channel", "name": "QQ 通道", "category": "service",
            "version": "0.0.1", "default_enabled": True,
            # 通道由 manifest 声明：没有 channel 块就不算通道（catalog.channels() 只认这个）
            "channel": {"kind": "qq", "label": "QQ", "pairing": True, "supports_group": True},
        }, ensure_ascii=False), encoding="utf-8")
        (d / "plugin.py").write_text(FAKE_PLUGIN, encoding="utf-8")

    def __enter__(self):
        from app.services.plugin import catalog
        self.catalog = catalog
        self.old = (catalog.BUILTIN_PLUGIN_DIR, catalog.USER_PLUGIN_DIR)
        catalog.BUILTIN_PLUGIN_DIR = self.root
        catalog.USER_PLUGIN_DIR = self.root / "user"
        return self

    def __exit__(self, *exc):
        self.catalog.BUILTIN_PLUGIN_DIR, self.catalog.USER_PLUGIN_DIR = self.old
        shutil.rmtree(self.root, ignore_errors=True)
        return False


async def _seed(db) -> tuple[int, int, int]:
    """一个 AI 主人 + 一个旁观者 + 一个 AI；返回 (owner_id, other_id, agent_id)"""
    from app.models.agent import Agent
    from app.models.plugin import Plugin
    from app.models.user import User
    from app.utils.auth import hash_password

    from db_reset import clear
    await clear(db, "external_identities", "plugin_configs", "plugin_service_states", "plugins",
                "agents", "users")
    owner = User(username="owner-a", password_hash=hash_password("x" * 12), email="a@test.local", type="human")
    other = User(username="other-b", password_hash=hash_password("x" * 12), email="b@test.local", type="human")
    db.add_all([owner, other])
    await db.flush()
    # AI 也有自己的用户行（agents.user_id 指向它）——群成员表里的 AI 用的是这个 id
    ai_user = User(username="小明", password_hash=hash_password("x" * 12), email="ai@test.local", type="ai")
    db.add(ai_user)
    await db.flush()
    agent = Agent(owner_id=owner.id, user_id=ai_user.id, name="小明")
    db.add(agent)
    await db.flush()
    db.add(Plugin(id="qq-channel", name="QQ 通道", category="service", enabled=True, builtin=True))
    await db.commit()
    return int(owner.id), int(other.id), int(agent.id)


async def test_pairing_lifecycle(migrated_db):
    from app.database import async_session
    from app.services.plugin import pairing

    async with async_session() as db:
        from db_reset import clear
        await clear(db, "external_identities")
        await db.commit()

        row = await pairing.upsert_pending(
            db, kind="qq", owner_scope="agent-1", origin="openid-aaa", display_name="小明"
        )
        assert row.status == "pending" and len(row.code) == 6
        assert await pairing.status_of(db, kind="qq", owner_scope="agent-1", origin="openid-aaa") == "pending"

        # 同一个人反复私聊：复用同一个码，不会刷出一串不同的码
        again = await pairing.upsert_pending(
            db, kind="qq", owner_scope="agent-1", origin="openid-aaa", display_name="小明"
        )
        assert again.id == row.id and again.code == row.code

        # 配对其他实例/其他机器人互相独立
        other = await pairing.upsert_pending(
            db, kind="qq", owner_scope="agent-2", origin="openid-aaa"
        )
        assert other.owner_scope == "agent-2" and other.code != "" 

        # 用户把码抄回来批准（大小写、空格都容忍）
        approved = await pairing.approve(
            db, kind="qq", owner_scope="agent-1", code=" " + row.code.lower() + " "
        )
        assert approved.status == "approved" and approved.approved_at is not None

        # 抄错码：报错而不是静默批准
        try:
            await pairing.approve(db, kind="qq", owner_scope="agent-1", code="XXXXXX")
            raise AssertionError("错码必须被拒绝")
        except ValueError:
            pass

        # 拉黑之后不许批准；解除之后回到陌生人
        await pairing.upsert_pending(db, kind="qq", owner_scope="agent-1", origin="openid-bbb")
        await pairing.set_status(db, kind="qq", owner_scope="agent-1", origin="openid-bbb", status="blocked")
        try:
            await pairing.approve(db, kind="qq", owner_scope="agent-1", origin="openid-bbb")
            raise AssertionError("拉黑的人不该被批准")
        except ValueError:
            pass
        assert await pairing.forget(db, kind="qq", owner_scope="agent-1", origin="openid-bbb") is True
        assert await pairing.status_of(db, kind="qq", owner_scope="agent-1", origin="openid-bbb") is None

        rows = await pairing.list_rows(db, kind="qq", owner_scope="agent-1")
        assert [r.origin for r in rows] == ["openid-aaa"]


async def test_channel_ownership_and_save(migrated_db):
    from app.database import async_session
    from app.services.plugin import channel, config as plugin_config, skill_bridge

    with _FakePluginDir():
        async with async_session() as db:
            owner_id, other_id, agent_id = await _seed(db)

            # 归属：不是自己的 AI，连看都看不到
            await channel.owned_agent(db, agent_id, owner_id)
            try:
                await channel.owned_agent(db, agent_id, other_id)
                raise AssertionError("别人的 AI 不该可见")
            except channel.NotOwned:
                pass

            instance = channel.instance_of(agent_id)
            assert instance == f"agent-{agent_id}"
            assert channel.agent_id_of(instance) == agent_id
            assert channel.agent_id_of("admin-made") is None

            # 保存：目标 AI 由路径决定，用户传什么都不算
            result = await channel.save(
                db, plugin_id="qq-channel", agent_id=agent_id, user_id=owner_id,
                values={"app_id": "1024", "client_secret": "s3cr3t", "target_agent": "别人的 AI"},
                actor="owner-a", target_agent_name="小明",
            )
            assert result["running"] is True and result["warning"] == ""
            cfg = await plugin_config.get_config("qq-channel", instance, db=db)
            assert cfg["target_agent"] == "小明"
            assert cfg["client_secret"] == "s3cr3t"

            view = (await channel.views(db, agent_id, owner_id))[0]
            assert view["running"] is True
            assert view["secrets"]["client_secret"] is True
            assert "s3cr3t" not in json.dumps(view, ensure_ascii=False, default=str)
            assert view["missing_required"] == []
            assert view["owner"] is None and view["pending"] == []

            # 空提交 = 什么都不改（允许部分保存的代价：这里必须靠"没传的键不动"兜住）
            await channel.save(db, plugin_id="qq-channel", agent_id=agent_id, user_id=owner_id, values={}, actor="owner-a", target_agent_name="小明")
            cfg_after = await plugin_config.get_config("qq-channel", instance, db=db)
            assert cfg_after["app_id"] == "1024" and cfg_after["client_secret"] == "s3cr3t", cfg_after

            # 管理员关掉插件 → 用户侧一律不许配
            from app.models.plugin import Plugin

            row = await db.get(Plugin, "qq-channel")
            row.enabled = False
            await db.commit()
            try:
                await channel.save(db, plugin_id="qq-channel", agent_id=agent_id, user_id=owner_id, values={"app_id": "1", "client_secret": "2"}, actor="owner-a", target_agent_name="小明")
                raise AssertionError("插件被全局关闭时不该能配")
            except PermissionError:
                pass
            row.enabled = True
            await db.commit()

            for plugin_id in list(skill_bridge._loaded):
                await skill_bridge._unload_plugin(plugin_id)


async def test_channel_self_test_needs_a_live_instance(migrated_db):
    """自测：画不画按钮由插件的声明说了算；通道没起来就没有可测的出口（404 的料，不是 500）"""
    from app.database import async_session
    from app.services.plugin import channel, skill_bridge

    with _FakePluginDir():
        async with async_session() as db:
            owner_id, _other, agent_id = await _seed(db)

            try:
                await channel.self_test(plugin_id="qq-channel", agent_id=agent_id)
                raise AssertionError("没配置的通道不该能自测")
            except channel.UnknownInstance:
                pass

            await channel.save(
                db, plugin_id="qq-channel", agent_id=agent_id, user_id=owner_id,
                values={"app_id": "1", "client_secret": "2"},
                actor="owner-a", target_agent_name="小明",
            )
            view = (await channel.views(db, agent_id, owner_id))[0]
            assert view["self_test"] is True, "插件声明了自测，卡片就该知道"

            result = await channel.self_test(plugin_id="qq-channel", agent_id=agent_id)
            assert result["sent"] is True and "自测" in result["text"], result

            for plugin_id in list(skill_bridge._loaded):
                await skill_bridge._unload_plugin(plugin_id)


async def test_admin_replace_keeps_owner_scoped(migrated_db):
    """管理台那份"列表即真相"不能把用户给自己 AI 建的通道删掉"""
    from app.database import async_session
    from app.services.plugin import channel, config as plugin_config, skill_bridge

    with _FakePluginDir():
        async with async_session() as db:
            owner_id, _other, agent_id = await _seed(db)
            await channel.save(
                db, plugin_id="qq-channel", agent_id=agent_id, user_id=owner_id,
                values={"app_id": "1", "client_secret": "2"},
                actor="owner-a", target_agent_name="小明",
            )
            user_instance = channel.instance_of(agent_id)

            # 管理台保存自己那份列表（只有一个 admin-made 实例）→ 用户实例必须留着
            await plugin_config.replace_instances(
                "qq-channel", [{"instance": "admin-made", "values": {"app_id": "9", "client_secret": "9"}}],
                actor="admin", db=db,
            )
            instances = await plugin_config.list_instances("qq-channel", db=db)
            assert user_instance in instances and "admin-made" in instances

            # 管理台把自己那个删掉（列表为空）→ 仍然只删自己那个
            await plugin_config.replace_instances("qq-channel", [], actor="admin", db=db)
            instances = await plugin_config.list_instances("qq-channel", db=db)
            assert instances == [user_instance]

            for plugin_id in list(skill_bridge._loaded):
                await skill_bridge._unload_plugin(plugin_id)


async def test_partial_save_keeps_secret(migrated_db):
    """部分保存（只改白名单/策略）不能抹掉已存的机密

    踩过的坑：set_config 的语义是"空串=清除、没传的键不动"，前端一键加白名单时
    把空的 client_secret 一起回传，结果把用户填好的 AppSecret 抹掉了。
    """
    from app.database import async_session
    from app.services.plugin import channel, config as plugin_config, skill_bridge

    with _FakePluginDir():
        async with async_session() as db:
            owner_id, _other, agent_id = await _seed(db)
            await channel.save(
                db, plugin_id="qq-channel", agent_id=agent_id, user_id=owner_id,
                values={"app_id": "1", "client_secret": "sec-keep-me"},
                actor="owner-a", target_agent_name="小明",
            )
            cfg = await plugin_config.get_config("qq-channel", channel.instance_of(agent_id), db=db)
            assert cfg["client_secret"] == "sec-keep-me"

            # 只提交白名单：凭据必须原封不动
            await channel.save(
                db, plugin_id="qq-channel", agent_id=agent_id, user_id=owner_id,
                values={"qq_group_allowlist": "GROUP-1"},
                actor="owner-a", target_agent_name="小明",
            )
            cfg = await plugin_config.get_config("qq-channel", channel.instance_of(agent_id), db=db)
            assert cfg["client_secret"] == "sec-keep-me", "部分保存把机密抹掉了"
            assert cfg["app_id"] == "1"
            assert cfg["qq_group_allowlist"] == "GROUP-1"

            for plugin_id in list(skill_bridge._loaded):
                await skill_bridge._unload_plugin(plugin_id)


async def test_group_binding_must_be_owned_and_joined(migrated_db):
    """接群的两个条件：群是你管的 + 这个 AI 已经在里面（否则 QQ 消息会落进别人的群）"""
    from app.database import async_session
    from app.models.group import Group, GroupMember
    from app.services.plugin import channel, skill_bridge

    with _FakePluginDir():
        async with async_session() as db:
            owner_id, other_id, agent_id = await _seed(db)
            from app.models.agent import Agent

            agent = await db.get(Agent, agent_id)

            # 别人的群（AI 在里面）：不能接
            foreign = Group(name="别人的群", owner_type="human", owner_id=other_id, avatar_mode="default", include_ai_in_avatar=True)
            db.add(foreign)
            await db.flush()
            db.add(GroupMember(group_id=foreign.id, member_type="ai", member_id=agent.user_id, role="member"))
            await db.commit()
            try:
                await channel.save(db, plugin_id="qq-channel", agent_id=agent_id, user_id=owner_id,
                                   values={"app_id": "1", "client_secret": "2", "copree_group_id": str(foreign.id)},
                                   actor="owner-a", target_agent_name="小明")
                raise AssertionError("别人的群不该能接")
            except ValueError:
                pass

            # 我管但 AI 不在里面的群：不能接
            mine = Group(name="我的空群", owner_type="human", owner_id=owner_id, avatar_mode="default", include_ai_in_avatar=True)
            db.add(mine)
            await db.commit()
            try:
                await channel.save(db, plugin_id="qq-channel", agent_id=agent_id, user_id=owner_id,
                                   values={"app_id": "1", "client_secret": "2", "copree_group_id": str(mine.id)},
                                   actor="owner-a", target_agent_name="小明")
                raise AssertionError("AI 不在里面的群不该能接")
            except ValueError:
                pass

            # 我管且 AI 在里面：可以接，而且下拉里只会出现这一个
            db.add(GroupMember(group_id=mine.id, member_type="ai", member_id=agent.user_id, role="member"))
            await db.commit()
            options = await channel.group_options(db, agent_id, owner_id)
            assert [g["id"] for g in options] == [mine.id], options

            result = await channel.save(db, plugin_id="qq-channel", agent_id=agent_id, user_id=owner_id,
                                        values={"app_id": "1", "client_secret": "2", "copree_group_id": str(mine.id)},
                                        actor="owner-a", target_agent_name="小明")
            assert result["running"] is True, result
            from app.services.plugin import config as plugin_config

            cfg = await plugin_config.get_config("qq-channel", channel.instance_of(agent_id), db=db)
            assert cfg["copree_group_id"] == str(mine.id)
            view = (await channel.views(db, agent_id, owner_id))[0]
            assert [g["id"] for g in view["group_options"]] == [mine.id]

            for plugin_id in list(skill_bridge._loaded):
                await skill_bridge._unload_plugin(plugin_id)


async def test_channel_routes_registered():
    from app.routers import channels

    paths = {getattr(r, "path", "") for r in channels.router.routes}
    for expected in (
        "/me/agents/{agent_id}/channels",
        "/me/agents/{agent_id}/channels/{plugin_id}",
        "/me/agents/{agent_id}/channels/{plugin_id}/start",
        "/me/agents/{agent_id}/channels/{plugin_id}/stop",
        "/me/agents/{agent_id}/channels/{plugin_id}/self-test",
        "/me/agents/{agent_id}/channels/{plugin_id}/landing-group",
        "/me/agents/{agent_id}/channels/{plugin_id}/pairings/approve",
        "/me/agents/{agent_id}/channels/{plugin_id}/pairings/block",
        "/me/agents/{agent_id}/channels/{plugin_id}/pairings/forget",
    ):
        assert expected in paths, "缺少路由 " + expected


async def test_channel_registry_is_manifest_driven():
    """通道清单来自插件声明：只有带 channel 块的插件才算通道，未知 plugin_id 是 404 的料"""
    from app.services.plugin import catalog, channel

    with _FakePluginDir():
        declared = {c["plugin_id"]: c for c in catalog.channels()}
        assert set(declared) == {"qq-channel"}, declared
        assert declared["qq-channel"]["kind"] == "qq"
        assert declared["qq-channel"]["pairing"] is True
        assert channel.declared("qq-channel")["label"] == "QQ"
        try:
            channel.declared("no-such-channel")
            raise AssertionError("没声明 channel 块的不该算通道")
        except channel.UnknownChannel:
            pass

    # 真插件目录：两条 QQ 通道各自声明类别，平台侧没有插件 id 常量表
    real = {c["plugin_id"]: c for c in catalog.channels()}
    assert real["qq-channel"]["kind"] == "qq"
    assert real["qq-napcat"]["kind"] == "qq-napcat"
    assert real["qq-napcat"]["label"] != real["qq-channel"]["label"]

async def test_group_brief_tells_ai_the_channel_rules(migrated_db):
    """接了通道的群：AI 上下文里要有「我在群里只能被动回复」这句话

    不然它会答应「我待会儿在群里提醒你」，而腾讯自 2025-04-21 起下线了主动推送——
    答应的事根本发不出去。
    """
    from app.database import async_session
    from app.services.plugin import channel, config as plugin_config, skill_bridge

    with _FakePluginDir():
        async with async_session() as db:
            owner_id, _other, agent_id = await _seed(db)
            assert await channel.group_brief(db, 999) == "", "没接通道的群不该多话"

            skill_bridge.ensure_declared("qq-channel")
            await plugin_config.set_config(
                "qq-channel",
                {"app_id": "1", "client_secret": "2", "copree_group_id": "7"},
                channel.instance_of(agent_id), db=db,
            )
            brief = await channel.group_brief(db, 7)
            assert "被动回复" in brief, brief
            assert "2025-04-21" in brief and "2 条" in brief, brief
            # 两条通道限制也要讲清：@其他成员的内容不转发；QQ 里别人撤回我们收不到
            assert "@其他成员" in brief and "撤回" in brief, brief

            for plugin_id in list(skill_bridge._loaded):
                await skill_bridge._unload_plugin(plugin_id)


async def test_group_brief_follows_the_observed_push_mode(migrated_db):
    """「@其他成员」那条规矩跟着群的实际模式走：观测到就直说，观测不到两句都讲（不能猜）

    2026-09-26 真机：全量模式开着时正文是完整的、@ 会显示成 <@!id>；插件靠事件类型判，
    平台这边问活着的实例（不落库——它是运行期观测，不是配置；持久记录在账本里）。
    """
    from app.database import async_session
    from app.services.infrastructure.plugin_registry import PluginRegistry, registry_key
    from app.services.plugin import channel, config as plugin_config, skill_bridge

    class _LiveChannel:
        """顶替真插件实例：只要 key（注册表索引）、name（注册日志）和模式观测"""

        def __init__(self, key: str, name: str, mode: bool | None) -> None:
            self.key = key
            self.name = name
            self.mode = mode

        def observed_full_mode(self) -> bool | None:
            return self.mode

    with _FakePluginDir():
        async with async_session() as db:
            _owner_id, _other, agent_id = await _seed(db)
            skill_bridge.ensure_declared("qq-channel")
            instance = channel.instance_of(agent_id)
            await plugin_config.set_config(
                "qq-channel",
                {"app_id": "1", "client_secret": "2", "copree_group_id": "7"},
                instance, db=db,
            )
            key = registry_key("qq-channel", instance)
            for mode, must_have, must_not in (
                (None, "全量消息时正文是完整的", None),
                (True, "开着官方**全量消息**", "不转发「@其他成员」"),
                (False, "不转发「@其他成员」", "开着官方**全量消息**"),
            ):
                PluginRegistry.register(_LiveChannel(key, "测试通道", mode))
                try:
                    brief = await channel.group_brief(db, 7)
                finally:
                    PluginRegistry.unregister(key)
                assert must_have in brief, (mode, brief)
                if must_not:
                    assert must_not not in brief, (mode, brief)

            for plugin_id in list(skill_bridge._loaded):
                await skill_bridge._unload_plugin(plugin_id)


