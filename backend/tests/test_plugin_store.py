"""插件商城一期：安装包校验 → 安装/更新 → 卸载（文件层 + DB 对齐）

四层：包结构（根目录 / 套一层）→ 安全过滤（越界、符号链接、隐藏目录、可执行后缀、条目与体积上限）
→ 安装语义（撞内置拒绝、重复安装必须显式 upgrade、覆盖不留残留）→ 与 DB 对齐（卸载即回收）。

用例把 catalog.USER_PLUGIN_DIR 与 store.PACKAGE_DIR 换到临时目录，不碰真实插件目录，
也不依赖 pytest 的 monkeypatch/tmp_path（后端容器只跑自带的极简运行器）。
"""
import io
import json
import shutil
import tempfile
import zipfile
from pathlib import Path

from sqlalchemy import text


def _manifest(**kw) -> str:
    data = {
        "id": "demo-plugin",
        "name": "演示插件",
        "description": "用例用",
        "category": "other",
        "version": "1.0.0",
        "author": "test",
    }
    data.update(kw)
    return json.dumps(data, ensure_ascii=False)


def _zip(files: dict, symlink: str | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
        if symlink:
            info = zipfile.ZipInfo(symlink)
            info.external_attr = 0o120777 << 16      # 符号链接标记位
            zf.writestr(info, "/etc/passwd")
    return buf.getvalue()


class _Sandbox:
    """把扫描根与包仓库换到临时目录（store/catalog 都是运行时读模块属性，所以改属性即可）"""

    def __enter__(self):
        from app.services.plugin import catalog, store

        self.root = Path(tempfile.mkdtemp(prefix="plugin-store-"))
        self.catalog, self.store = catalog, store
        self.old = (catalog.BUILTIN_PLUGIN_DIR, catalog.USER_PLUGIN_DIR, store.PACKAGE_DIR)
        catalog.BUILTIN_PLUGIN_DIR = self.root / "builtin"
        catalog.USER_PLUGIN_DIR = self.root / "plugins"
        store.PACKAGE_DIR = self.root / "packages"
        (self.root / "builtin").mkdir()
        return self

    def builtin(self, plugin_id: str) -> Path:
        d = self.catalog.BUILTIN_PLUGIN_DIR / plugin_id
        d.mkdir(parents=True)
        (d / "plugin.json").write_text(_manifest(id=plugin_id), encoding="utf-8")
        return d

    def __exit__(self, *exc):
        self.catalog.BUILTIN_PLUGIN_DIR, self.catalog.USER_PLUGIN_DIR, self.store.PACKAGE_DIR = self.old
        shutil.rmtree(self.root, ignore_errors=True)
        return False


async def test_inspect_and_install_plain_package():
    from app.services.plugin import catalog, store

    with _Sandbox() as box:
        data = _zip({
            "plugin.json": _manifest(id="demo-skin", category="skin", version="1.2.0"),
            "skin.json": json.dumps({"light": {"primary_500": "#123456"}}),
            "assets/icon.png": b"\x89PNG\r\n",
        })
        info = store.inspect_package(data)
        assert info["manifest"]["id"] == "demo-skin"
        assert info["manifest"]["category"] == "skin"
        assert info["file_count"] == 3
        assert len(info["sha256"]) == 64 and info["sha256"] == info["sha256"].lower()

        result = store.install_package(data)
        target = catalog.USER_PLUGIN_DIR / "demo-skin"
        assert result["installed_version"] == "1.2.0"
        assert (target / "plugin.json").is_file()
        assert (target / "assets" / "icon.png").read_bytes() == b"\x89PNG\r\n"
        assert store._installed_version("demo-skin") == "1.2.0"
        # 安装过程不留 .staging / .trash 残渣——它们的存在意味着原子替换没走完
        assert [p.name for p in catalog.USER_PLUGIN_DIR.iterdir() if not p.name.startswith(".")] == ["demo-skin"]


async def test_install_unwraps_single_top_dir():
    from app.services.plugin import catalog, store

    with _Sandbox() as box:
        data = _zip({
            "copree-plugin-demo/plugin.json": _manifest(id="", category="skill", name="套一层"),
            "copree-plugin-demo/skill.json": "{}",
        })
        # manifest 里没有 id → 用顶层目录名兜底
        info = store.inspect_package(data)
        assert info["manifest"]["id"] == "copree-plugin-demo"
        store.install_package(data)
        target = catalog.USER_PLUGIN_DIR / "copree-plugin-demo"
        assert (target / "plugin.json").is_file(), "下钻一层后 plugin.json 必须落在插件根"
        assert (target / "skill.json").is_file()


async def test_unsafe_entries_are_skipped():
    from app.services.plugin import catalog, store

    with _Sandbox() as box:
        data = _zip(
            {
                "plugin.json": _manifest(id="demo-safe"),
                "ok.py": "print('ok')\n",
                "../escape.py": "evil\n",
                "/absolute.py": "evil\n",
                ".hidden/secret.py": "evil\n",
                "__pycache__/cached.pyc": "evil\n",
                "run.sh": "rm -rf /\n",
                "node_modules/x.js": "x\n",
            },
            symlink="link.py",
        )
        info = store.inspect_package(data)
        assert sorted(info["files"]) == ["ok.py", "plugin.json"]

        store.install_package(data)
        installed = sorted(
            str(p.relative_to(catalog.USER_PLUGIN_DIR / "demo-safe")).replace("\\", "/")
            for p in (catalog.USER_PLUGIN_DIR / "demo-safe").rglob("*") if p.is_file()
        )
        assert installed == ["ok.py", "plugin.json"]
        # 越界条目也不能落在插件目录之外
        assert not (catalog.USER_PLUGIN_DIR.parent / "escape.py").exists()


async def test_package_rejections():
    from app.services.plugin import store

    with _Sandbox():
        cases = {
            "缺 manifest": {"readme.md": "hi"},
            "id 非法": {"plugin.json": _manifest(id="../evil")},
            "id 带空格": {"plugin.json": _manifest(id="bad id")},
            "类别非法": {"plugin.json": _manifest(category="hack")},
            "manifest 不是对象": {"plugin.json": "[1,2,3]"},
            "不是 zip": None,
        }
        for label, files in cases.items():
            data = _zip(files) if files else b"not a zip at all"
            try:
                store.inspect_package(data)
                raise AssertionError(f"{label} 必须被拒绝")
            except store.PackageError:
                pass


async def test_limits_and_manifest_depth():
    from app.services.plugin import store

    with _Sandbox():
        old_entries = store.MAX_ENTRIES
        store.MAX_ENTRIES = 2
        try:
            data = _zip({"plugin.json": _manifest(), "a.py": "1", "b.py": "2"})
            try:
                store.inspect_package(data)
                raise AssertionError("条目数超限必须被拒绝")
            except store.PackageError:
                pass
        finally:
            store.MAX_ENTRIES = old_entries

        deep = _zip({"a/b/plugin.json": _manifest(id="deep")})
        try:
            store.inspect_package(deep)
            raise AssertionError("manifest 层级过深必须被拒绝")
        except store.PackageError:
            pass

        twice = _zip({"plugin.json": _manifest(id="x"), "sub/plugin.json": _manifest(id="y")})
        try:
            store.inspect_package(twice)
            raise AssertionError("多个 manifest 必须被拒绝")
        except store.PackageError:
            pass


async def test_builtin_id_conflict_refused():
    from app.services.plugin import store

    with _Sandbox() as box:
        box.builtin("qq-channel")
        data = _zip({"plugin.json": _manifest(id="qq-channel", name="伪装的内置")})
        try:
            store.install_package(data)
            raise AssertionError("用户包不得覆盖内置插件")
        except store.PackageError:
            pass
        assert store.is_builtin("qq-channel")


async def test_upgrade_replaces_and_cleans_old_files():
    from app.services.plugin import catalog, store

    with _Sandbox() as box:
        v1 = _zip({"plugin.json": _manifest(id="demo-up", version="1.0.0"), "old.py": "old\n"})
        store.install_package(v1)

        try:
            store.install_package(v1)
            raise AssertionError("已安装时重复安装必须要求显式 upgrade")
        except store.PackageError:
            pass

        v2 = _zip({"plugin.json": _manifest(id="demo-up", version="2.0.0"), "new.py": "new\n"})
        store.install_package(v2, upgrade=True)
        target = catalog.USER_PLUGIN_DIR / "demo-up"
        assert store._installed_version("demo-up") == "2.0.0"
        assert (target / "new.py").is_file()
        assert not (target / "old.py").exists(), "覆盖安装必须整体替换，不能新旧混在一起"
        assert [p.name for p in catalog.USER_PLUGIN_DIR.iterdir() if p.name.startswith(".")] == []


async def test_uninstall_rules():
    from app.services.plugin import store

    with _Sandbox() as box:
        box.builtin("builtin-x")
        store.install_package(_zip({"plugin.json": _manifest(id="demo-del")}))
        store.uninstall_plugin("demo-del")
        assert not (box.catalog.USER_PLUGIN_DIR / "demo-del").exists()

        for label, plugin_id in (("内置不可卸", "builtin-x"), ("重复卸载", "demo-del"), ("id 非法", "../x")):
            try:
                store.uninstall_plugin(plugin_id)
                raise AssertionError(f"{label} 必须失败")
            except store.PackageError:
                pass
        assert box.catalog.BUILTIN_PLUGIN_DIR.joinpath("builtin-x").is_dir()


async def test_package_repository_list_save_delete():
    from app.services.plugin import store

    with _Sandbox():
        good = _zip({"plugin.json": _manifest(id="demo-pkg", version="0.9.0")})
        saved = store.save_package("demo-pkg-0.9.0.zip", good)
        assert saved["file_name"] == "demo-pkg-0.9.0.zip"
        assert saved["installed"] is False and saved["updatable"] is False

        # 没有扩展名的上传走 id-version 命名；坏包不落盘
        store.save_package("noext", _zip({"plugin.json": _manifest(id="demo-pkg2")}))
        assert (store.PACKAGE_DIR / "demo-pkg2-1.0.0.zip").is_file()
        try:
            store.save_package("broken.zip", b"nope")
            raise AssertionError("坏包不能落盘")
        except store.PackageError:
            pass
        assert not (store.PACKAGE_DIR / "broken.zip").exists()

        # 仓库里混进坏包：列表标记 error，但不影响其它条目
        (store.PACKAGE_DIR / "junk.zip").write_bytes(b"nope")
        packages = {p["file_name"]: p for p in store.list_packages()}
        assert "error" in packages["junk.zip"]
        assert packages["demo-pkg-0.9.0.zip"]["manifest"]["id"] == "demo-pkg"

        store.delete_package("demo-pkg-0.9.0.zip")
        assert not (store.PACKAGE_DIR / "demo-pkg-0.9.0.zip").exists()
        try:
            store.delete_package("../outside.zip")
            raise AssertionError("借 .. 删仓库外的文件必须失败")
        except store.PackageError:
            pass


async def test_store_install_uninstall_aligns_db(migrated_db):
    """端到端：装包 → 磁盘 → DB 出现非内置行；卸载 → 行随之消失（路由层就是这两步）"""
    from app.database import async_session
    from app.models.plugin import Plugin
    from app.services.plugin import catalog, store
    from sqlalchemy import select

    with _Sandbox() as box:
        async with async_session() as db:
            from db_reset import clear
            await clear(db, "plugins")
            await db.commit()

            store.install_package(_zip({"plugin.json": _manifest(id="demo-db", name="入库演示")}))
            changed = await catalog.sync_plugins_to_db(db)
            assert changed == 1
            row = await db.get(Plugin, "demo-db")
            assert row is not None and row.builtin is False and row.name == "入库演示"

            store.uninstall_plugin("demo-db")
            await catalog.sync_plugins_to_db(db)
            assert (await db.execute(select(Plugin).where(Plugin.id == "demo-db"))).scalar_one_or_none() is None


async def test_broadcast_and_router_helpers_importable():
    """路由模块导入即注册：store 接口依赖的 _after_store_change 必须在（防止改名后静默失效）"""
    from app.routers import plugins as plugins_router

    assert callable(plugins_router._after_store_change)
    paths = {getattr(r, "path", "") for r in plugins_router.router.routes}
    for expected in ("/plugins/store", "/plugins/store/packages",
                     "/plugins/store/packages/{file_name}/install",
                     "/plugins/store/packages/{file_name}",
                     "/plugins/store/installed/{plugin_id}"):
        assert expected in paths, f"缺少路由 {expected}"
