"""插件包导入：插件目录就是一个包。

作者约定：入口文件（默认 plugin.py）之外，同目录的其它 .py 用相对导入取用；
重扫/卸载会把包和子模块一起换掉，改过的兄弟文件立刻生效。
"""
import sys
import tempfile
import textwrap
from pathlib import Path

PLUGIN_ID = "demo-multi"


def _write_plugin(root: Path, answer: int = 42, note: str = "") -> None:
    (root / "plugin.py").write_text(textwrap.dedent("""
        from .helper import ANSWER

        PLUGIN_ANSWER = ANSWER
    """), encoding="utf-8")
    # note 让重写后的文件长度不同：字节码缓存按 (mtime, size) 失效，
    # 同秒同长的改写会命中旧 pyc，那不是加载层的问题
    (root / "helper.py").write_text(f"ANSWER = {answer}{note}\n", encoding="utf-8")


def _leftover(plugin_id: str) -> list[str]:
    prefix = f"copree_plugins.{plugin_id}"
    return [k for k in sys.modules if k == prefix or k.startswith(prefix + ".")]


def test_plugin_dir_is_imported_as_a_package():
    from app.services.plugin import packages

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_plugin(root)
        module = packages.load(PLUGIN_ID, root)
        try:
            assert module.PLUGIN_ANSWER == 42
            assert f"copree_plugins.{PLUGIN_ID}.helper" in sys.modules
        finally:
            packages.forget(PLUGIN_ID)
        assert _leftover(PLUGIN_ID) == []


def test_reload_reads_the_edited_sibling_module():
    """重扫要读到作者改过的兄弟文件：旧的子模块缓存必须清掉。"""
    from app.services.plugin import packages

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_plugin(root, answer=42)
        assert packages.load(PLUGIN_ID, root).PLUGIN_ANSWER == 42
        _write_plugin(root, answer=43, note="  # 改过")
        try:
            assert packages.load(PLUGIN_ID, root).PLUGIN_ANSWER == 43
        finally:
            packages.forget(PLUGIN_ID)


def test_entry_file_can_be_declared():
    """入口文件名由 manifest 的 entry 决定，不写死 plugin.py。"""
    from app.services.plugin import packages

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "main.py").write_text("ENTRY = 'main'\n", encoding="utf-8")
        try:
            assert packages.load("demo-entry", root, "main.py").ENTRY == "main"
        finally:
            packages.forget("demo-entry")


def test_missing_entry_raises_and_leaves_nothing():
    from app.services.plugin import packages

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        try:
            packages.load("demo-missing", root)
        except FileNotFoundError:
            pass
        else:
            raise AssertionError("缺入口文件应当报错")
        assert _leftover("demo-missing") == []


def test_failed_import_leaves_no_package_behind():
    from app.services.plugin import packages

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "plugin.py").write_text("from .nope import X\n", encoding="utf-8")
        try:
            packages.load("demo-broken", root)
        except ImportError:
            pass
        else:
            raise AssertionError("导入失败应当抛出 ImportError")
        assert _leftover("demo-broken") == []
