"""插件包导入 — 每个插件目录就是一个 Python 包，挂在同一个命名空间下。

为什么不是"按路径 exec 一个文件"：
- 相对导入（`from .gateway import QqClient`）需要一个可查的父包，否则作者只能把所有代码塞进
  一个文件；
- 重扫要连子模块一起换掉，否则作者改了兄弟文件、跑的还是旧的；
- traceback / 调试器里出现的是 `copree_plugins.qq-channel.gateway` 这种正常名字，不是
  按路径现编的临时模块名。

所以这里注册一个 MetaPathFinder：`copree_plugins.<插件 id>` → 插件目录（入口文件由 manifest
的 entry 决定，缺省 plugin.py）。导入就是普通 import，重载就是"清掉再导入"。
finder 只在导入那一刻被问一次，收发消息的热路径不经过它。
"""
from __future__ import annotations

import importlib
import importlib.abc
import importlib.util
import logging
import sys
from pathlib import Path
from types import ModuleType

logger = logging.getLogger(__name__)

# 所有插件包都在这下面：命名空间本身不是实体目录，由 ensure_namespace() 造一个空包
NAMESPACE = "copree_plugins"
DEFAULT_ENTRY = "plugin.py"


def package_name(plugin_id: str) -> str:
    return f"{NAMESPACE}.{plugin_id}"


def ensure_namespace() -> None:
    """命名空间父包必须在 sys.modules 里：没有它，import 机制查不到插件的父包。"""
    if NAMESPACE in sys.modules:
        return
    spec = importlib.util.spec_from_loader(NAMESPACE, loader=None, is_package=True)
    module = importlib.util.module_from_spec(spec)
    module.__path__ = []                      # 路径由下面的 finder 负责，不看文件系统
    sys.modules[NAMESPACE] = module


class _PluginFinder(importlib.abc.MetaPathFinder):
    """把 <NAMESPACE>.<id> 解析到插件目录。只认这一段前缀，别的导入一概不碰。"""

    def __init__(self) -> None:
        self._roots: dict[str, tuple[Path, str]] = {}

    def register(self, plugin_id: str, plugin_dir: Path, entry: str = DEFAULT_ENTRY) -> None:
        self._roots[plugin_id] = (Path(plugin_dir), entry or DEFAULT_ENTRY)

    def unregister(self, plugin_id: str) -> None:
        self._roots.pop(plugin_id, None)

    def find_spec(self, fullname: str, path=None, target=None):  # noqa: ANN001 — 标准签名
        prefix = NAMESPACE + "."
        if not fullname.startswith(prefix):
            return None
        plugin_id, _, submodule = fullname[len(prefix):].partition(".")
        if submodule or plugin_id not in self._roots:
            return None
        directory, entry = self._roots[plugin_id]
        entry_file = directory / entry
        if not entry_file.is_file():
            logger.warning("插件入口文件不存在：%s", entry_file)
            return None
        # 带 submodule_search_locations：这个模块同时是包，子模块由常规路径查找在目录里找
        return importlib.util.spec_from_file_location(
            fullname, str(entry_file), submodule_search_locations=[str(directory)])


_finder = _PluginFinder()
if _finder not in sys.meta_path:
    sys.meta_path.insert(0, _finder)


def register(plugin_id: str, plugin_dir: Path, entry: str = DEFAULT_ENTRY) -> None:
    ensure_namespace()
    _finder.register(plugin_id, plugin_dir, entry)
    importlib.invalidate_caches()


def forget(plugin_id: str) -> None:
    """丢掉这个插件的包与全部子模块：卸载、重扫前调用，保证下次导入读到磁盘上的新文件。"""
    name = package_name(plugin_id)
    for key in [k for k in sys.modules if k == name or k.startswith(name + ".")]:
        sys.modules.pop(key, None)
    _finder.unregister(plugin_id)
    importlib.invalidate_caches()


def load(plugin_id: str, plugin_dir: Path, entry: str = DEFAULT_ENTRY) -> ModuleType:
    """（重新）导入插件包，返回入口模块。失败不留半成品：包与子模块一并清掉。"""
    entry_file = Path(plugin_dir) / (entry or DEFAULT_ENTRY)
    if not entry_file.is_file():
        raise FileNotFoundError(f"插件入口文件不存在：{entry_file}")
    forget(plugin_id)
    register(plugin_id, plugin_dir, entry)
    try:
        return importlib.import_module(package_name(plugin_id))
    except Exception:
        forget(plugin_id)
        raise
