"""插件协议 v3：服务类插件（category=service）

四层：加载（目录 → 注册表）→ 配置与凭据（机密加密落库、接口不回显）→
启停与回收（不误伤内置服务）→ 空串即清除。

用例在临时目录里造一个 service 插件，并临时替换扫描根——不碰真实插件目录，
也不依赖 pytest 的 monkeypatch/tmp_path（后端容器只跑自带的极简运行器）。
"""
import json
import shutil
import tempfile
from pathlib import Path

from sqlalchemy import select, text

PLUGIN_PY = '''
from pathlib import Path

from app.services.plugin.api import service, ServicePlugin

MARK = Path(__file__).parent / "events.txt"


def _mark(line):
    with open(MARK, "a", encoding="utf-8") as fh:
        fh.write(line + "\\n")


@service(
    name="QQ Demo",
    description="协议用例用的假服务插件",
    config_schema={
        "app_id": {"type": "string", "title": "AppID", "required": True},
        "client_secret": {"type": "string", "title": "ClientSecret", "secret": True, "required": True},
    },
)
class QqDemo(ServicePlugin):
    def __init__(self):
        self._running = False

    async def get_status(self):
        return {"installed": True, "running": self._running}

    async def start(self):
        cfg = await self.config()
        _mark("start:%s:%s" % (cfg.get("app_id"), cfg.get("client_secret")))
        if not cfg.get("app_id"):
            return False
        self._running = True
        return True

    async def stop(self):
        _mark("stop")
        self._running = False
        return True
'''

MANIFEST = {
    "id": "qq-demo",
    "name": "QQ Demo",
    "description": "协议用例用的假服务插件",
    "category": "service",
    "version": "0.0.1",
    "author": "test",
}


async def test_service_plugin_lifecycle_and_secrets(migrated_db):
    from app.database import async_session
    from app.models.plugin import Plugin, PluginConfig
    from app.services.infrastructure.plugin_registry import PluginRegistry
    from app.services.plugin import catalog, skill_bridge
    from app.services.plugin.config import get_config, mask_config, set_config

    root = Path(tempfile.mkdtemp(prefix="plugin-v3-"))
    plugin_dir = root / "qq-demo"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(json.dumps(MANIFEST, ensure_ascii=False), encoding="utf-8")
    (plugin_dir / "plugin.py").write_text(PLUGIN_PY, encoding="utf-8")
    mark = plugin_dir / "events.txt"

    old_builtin, old_user = catalog.BUILTIN_PLUGIN_DIR, catalog.USER_PLUGIN_DIR
    catalog.BUILTIN_PLUGIN_DIR, catalog.USER_PLUGIN_DIR = root, root / "user"
    try:
        async with async_session() as db:
            from db_reset import clear
            await clear(db, "plugin_configs", "plugins")
            db.add(Plugin(id="qq-demo", name="QQ Demo", category="service", enabled=True, builtin=True))
            await db.commit()

            # ── 第 1 层：加载 ──
            await skill_bridge.apply_skill_plugins(db)
            # 不断言"注册表里只有它"：同一个进程里别的用例可能已经导入了内置服务插件
            assert "qq-demo" in {p.id for p in PluginRegistry.get_all()}
            plugin = PluginRegistry.get("qq-demo")
            assert plugin is not None
            assert plugin.owner == "qq-demo"          # owner 是回收的依据，错了就会误删内置服务
            assert "client_secret" in plugin.config_schema
            await skill_bridge.apply_skill_plugins(db)
            assert PluginRegistry.get("qq-demo") is plugin, "重复 apply 不该换实例（会丢运行态）"

            # ── 第 2 层：配置与凭据 ──
            changed = await set_config(
                "qq-demo", {"app_id": "12345", "client_secret": "s3cr3t-value"}, actor="test", db=db
            )
            assert sorted(changed) == ["app_id", "client_secret"]
            rows = {
                r.key: r
                for r in (await db.execute(
                    select(PluginConfig).where(PluginConfig.plugin_id == "qq-demo")
                )).scalars().all()
            }
            assert rows["app_id"].value == "12345"
            assert "s3cr3t-value" not in (rows["client_secret"].value or ""), "机密项必须是密文"
            assert rows["client_secret"].is_secret is True

            assert (await get_config("qq-demo", db=db))["client_secret"] == "s3cr3t-value"
            masked = await mask_config("qq-demo", db=db)
            assert masked["secrets"]["client_secret"] is True
            assert "s3cr3t-value" not in json.dumps(masked, ensure_ascii=False), "接口永不回显机密"

            try:
                await set_config("qq-demo", {"nope": "x"}, db=db)
                raise AssertionError("schema 之外的键必须被拒绝")
            except ValueError:
                pass

            # ── 第 3 层：启停与回收 ──
            assert await plugin.start() is True
            assert (await plugin.get_status())["running"] is True
            assert "start:12345:s3cr3t-value" in mark.read_text(encoding="utf-8"), "插件拿到的是解密后的值"

            # 内置服务插件 owner 是 None，目录插件的回收路径不能把它摘掉
            import app.services.content.browser_plugin  # noqa: F401  导入即注册
            await skill_bridge._unload_service_plugin("browser")
            assert PluginRegistry.get("browser") is not None

            row = await db.get(Plugin, "qq-demo")
            row.enabled = False
            await db.commit()
            await skill_bridge.apply_skill_plugins(db)
            assert PluginRegistry.get("qq-demo") is None
            assert "stop" in mark.read_text(encoding="utf-8"), "摘除前必须先 stop()"

            # ── 第 4 层：空串 = 清除 ──
            row = await db.get(Plugin, "qq-demo")
            row.enabled = True
            await db.commit()
            await skill_bridge.apply_skill_plugins(db)
            await set_config("qq-demo", {"app_id": "", "client_secret": ""}, db=db)
            cfg = await get_config("qq-demo", db=db)
            assert cfg["app_id"] is None and cfg["client_secret"] is None
    finally:
        catalog.BUILTIN_PLUGIN_DIR, catalog.USER_PLUGIN_DIR = old_builtin, old_user
        for plugin_id in list(skill_bridge._loaded):
            await skill_bridge._unload_plugin(plugin_id)
        shutil.rmtree(root, ignore_errors=True)
