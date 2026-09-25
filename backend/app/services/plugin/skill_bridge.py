"""
插件桥接 — 把磁盘插件登记进运行时（技能 + 服务），统一入口

统一入口：插件的元数据与行为都从这里进出（单一来源）。

同一套目录契约，三种形态（同一目录里行为可选）：
- 声明式技能（skill.json）：只有元数据，无行为 —— 现有格式，完全兼容
- 行为式技能（plugin.py + @skill）：装饰器同时完成声明 + 行为注册
- 服务式（plugin.py + @service）：装饰器实例化并注册一个 ServicePlugin（常驻服务）

规则（"装好即可用"）：
- 插件全局 enabled（管理员开关）时，其技能类型对全平台可用、其服务被登记
- 管理员关闭 / 目录消失 → 先停服务，再注销技能类型与行为处理器
- 由插件注册的技能类型记录在 _from_plugins，注销时只动这些，绝不误删内置类型
- 行为处理器条目带 owner（来源插件 id），同名类型谁注册删谁
- 服务实例同样带 owner，回收时只摘 owner == 自己 的，不误删同名内置服务
"""
from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.services.plugin.catalog import scan_disk, get_skill_defs
from app.services.skill.skill_engine import _ACTION_HANDLERS, _INJECT_HANDLERS
from app.utils.pure.skill_registry import SkillRegistry

from app.repositories.plugin_repo import PluginRepository, SQLAlchemyPluginRepository
from sqlalchemy.ext.asyncio import AsyncSession
logger = logging.getLogger(__name__)

# 本模块负责的插件类别（其余类别由各自的消费方处理）
BRIDGED_CATEGORIES = ("skill", "service")


def _ensure_repo(db_or_repo):
    """兼容旧调用：传入 AsyncSession 时包装成 SQLAlchemyPluginRepository。"""
    if isinstance(db_or_repo, AsyncSession):
        return SQLAlchemyPluginRepository(db_or_repo)
    return db_or_repo


# {plugin_id: set(type_name)} — 由声明式插件注册的技能类型（行为式插件由 owner 追踪）
_from_plugins: dict[str, set[str]] = {}

# {plugin_id: category} — 已导入过 plugin.py 的插件。
# 重复导入会重新执行装饰器：技能插件只是白跑一遍，服务插件却会把正在跑的服务
# 实例顶掉（旧任务没人回收、状态显示未运行）——所以这里必须幂等。
_loaded: dict[str, str] = {}


def _load_plugin_code(plugin_id: str, plugin_dir: Path) -> bool:
    """导入插件目录的 plugin.py：@skill / @service 装饰器在导入时完成注册。

    owner 通过 api.set_current_plugin 注入。加载后清空上下文，防止模块顶层误注册。
    返回是否真的导入了（没有 plugin.py = 纯声明式插件）。
    """
    from app.services.plugin import api

    handlers_file = plugin_dir / "plugin.py"
    if not handlers_file.exists():
        return False
    spec = importlib.util.spec_from_file_location(
        f"_aisc_plugin_{plugin_id}", str(handlers_file)
    )
    if spec is None or spec.loader is None:
        logger.warning(f"插件代码加载失败（无法创建模块）: {plugin_id}")
        return False
    module = importlib.util.module_from_spec(spec)
    try:
        api.set_current_plugin(plugin_id)
        spec.loader.exec_module(module)
    except Exception as e:
        logger.warning(f"插件代码加载异常: {plugin_id}: {e}")
        return False
    finally:
        api.set_current_plugin(None)
    return True


def _unload_behavior_plugin(plugin_id: str) -> None:
    """回收某插件注册的全部行为处理器：从两个注册表删除 owner == plugin_id 的条目，
    并注销其注册的 SkillRegistry 元数据（行为插件元数据不经 _from_plugins 追踪）。"""
    removed: list[str] = []
    for table in (_ACTION_HANDLERS, _INJECT_HANDLERS):
        for type_name in [t for t, (owner, _fn) in table.items() if owner == plugin_id]:
            del table[type_name]
            removed.append(type_name)
    for type_name in removed:
        SkillRegistry.unregister(type_name)
    if removed:
        logger.info(f"行为处理器已回收: {plugin_id} → {sorted(removed)}")


def _register_plugin_skills(plugin_id: str, defs: list[dict[str, Any]]) -> None:
    registered: set[str] = set()
    for skill in defs:
        type_name = str(skill.get("type", "")).strip()
        if not type_name:
            continue
        SkillRegistry.register(
            type_name=type_name,
            name=str(skill.get("name", type_name))[:60],
            category=str(skill.get("category", "inject")),
            description=str(skill.get("description", "")),
            config_schema=skill.get("config_schema") or {},
        )
        registered.add(type_name)
    _from_plugins[plugin_id] = registered
    if registered:
        logger.info(f"技能插件已注册: {plugin_id} → {sorted(registered)}")


def _unregister_plugin_skills(plugin_id: str) -> None:
    for type_name in _from_plugins.pop(plugin_id, set()):
        SkillRegistry.unregister(type_name)


async def _unload_service_plugin(plugin_id: str) -> None:
    """停掉并摘除这个目录插件的**全部实例**。

    顺序不能反：先停再摘。反过来的话注册表里没了、后台任务还在跑，
    之后谁也回收不到它（重启也只能靠进程退出兜底）。
    keys_of 按 owner 过滤，所以内置服务（browser，owner=None）不会被误伤。
    """
    from app.services.infrastructure.plugin_registry import PluginRegistry
    from app.services.plugin import api

    for key in PluginRegistry.keys_of(plugin_id):
        plugin = PluginRegistry.get(key)
        if plugin is None:
            continue
        try:
            await plugin.stop()
        except Exception as e:
            logger.warning(f"服务插件停止失败（继续回收，避免半死不活）: {key}: {e}")
        PluginRegistry.unregister(key)
    api.drop_service_def(plugin_id)


def _instantiate_service(defn, instance: str):
    """按声明建一个实例并注册（实例只是"同一个插件的另一份配置"）"""
    from app.services.infrastructure.plugin_registry import PluginRegistry

    obj = defn.cls()
    obj.id = defn.plugin_id
    obj.instance = instance
    obj.name = defn.name
    obj.description = defn.description
    obj.config_schema = defn.config_schema
    obj.multi_instance = defn.multi_instance
    obj.owner = defn.plugin_id
    PluginRegistry.register(obj)
    return obj


async def _reconcile_service_instances(plugin_id: str, db) -> None:
    """让运行中的实例与数据库里的配置对齐：配置没了的停掉，新配的建起来。

    单实例插件固定一个实例（""）；多实例插件以 plugin_configs 里出现过的 instance 为准。
    这一步是幂等的，所以每次 apply 都可以跑——配置刚改完也能立刻生效。
    """
    from app.services.infrastructure.plugin_registry import PluginRegistry, registry_key
    from app.services.plugin import api, config as plugin_config

    defn = api.get_service_def(plugin_id)
    if defn is None:
        return
    wanted = [""] if not defn.multi_instance else await plugin_config.list_instances(plugin_id, db=db)
    wanted_keys = {registry_key(plugin_id, i) for i in wanted}

    for key in [k for k in PluginRegistry.keys_of(plugin_id) if k not in wanted_keys]:
        plugin = PluginRegistry.get(key)
        if plugin is None:
            continue
        try:
            await plugin.stop()
        except Exception as e:
            logger.warning(f"实例已从配置里删除，停止时出错: {key}: {e}")
        PluginRegistry.unregister(key)

    for instance in wanted:
        if PluginRegistry.get(registry_key(plugin_id, instance)) is None:
            _instantiate_service(defn, instance)


async def _unload_plugin(plugin_id: str) -> None:
    """完整卸载一个插件：服务 + 行为处理器 + 声明元数据。"""
    await _unload_service_plugin(plugin_id)
    _unload_behavior_plugin(plugin_id)
    _unregister_plugin_skills(plugin_id)
    _loaded.pop(plugin_id, None)


def ensure_declared(plugin_id: str) -> bool:
    """保证插件的运行时声明（@service 的 config_schema）已加载。

    为什么需要它：配置校验读的是运行时声明，而声明只在 import 插件代码时产生。
    管理台平时会 apply，但"用户第一次给自己的 AI 开通道"可能发生在谁都没触发过 apply 的时候，
    那时拿不到 schema，就会误报"插件没有声明 config_schema"。这里按需补一次 import。
    """
    from app.services.plugin import api

    if api.get_service_def(plugin_id) is not None:
        return True
    manifest = scan_disk().get(plugin_id)
    if not manifest:
        return False
    if _load_plugin_code(plugin_id, Path(manifest["_dir"])):
        _loaded[plugin_id] = str(manifest.get("category") or "service")
        return True
    return False


async def apply_skill_plugins(db, *, force: bool = False) -> None:
    """按 DB 全局开关应用/回收插件（启动 + 开关切换 + 重扫后调用）。

    只做登记与回收，**不启动服务**：启动是 bootstrap 与管理员显式点"启动"的事，
    不能因为一次 GET /plugins 就把后台服务拉起来。

    :param force: 重扫用——已经加载过的插件也重新导入代码（改了 plugin.py 立刻生效），
                  并在重载后把原本在跑的服务拉回运行态（重扫不该改变运行状态）
    """
    db = _ensure_repo(db)
    from app.models.plugin import Plugin

    disk = scan_disk()
    result = await db.execute(select(Plugin))
    db_plugins = {p.id: p for p in result.scalars().all()}

    running: dict[str, bool] = {}
    if force:
        from app.services.infrastructure.plugin_registry import PluginRegistry

        for plugin_id in list(_loaded):
            for key in PluginRegistry.keys_of(plugin_id):
                plugin = PluginRegistry.get(key)
                try:
                    running[key] = bool(plugin and (await plugin.get_status()).get("running"))
                except Exception:
                    running[key] = False
            await _unload_plugin(plugin_id)

    # 目录消失 / DB 行消失 → 回收（含行为式插件：它们不经 _from_plugins 追踪）
    for plugin_id in [pid for pid in _loaded if pid not in disk or pid not in db_plugins]:
        await _unload_plugin(plugin_id)

    # 已启用的 → 加载；已停用的 → 回收
    for plugin_id, manifest in disk.items():
        category = manifest.get("category")
        if category not in BRIDGED_CATEGORIES:
            continue
        row = db_plugins.get(plugin_id)
        enabled = row.enabled if row else bool(manifest.get("default_enabled", True))
        if not enabled:
            await _unload_plugin(plugin_id)
            continue
        if category == "service":
            # 声明只在导入时产生一次；实例每次都要跟配置对齐（配置可能刚改过）
            if plugin_id not in _loaded:
                if not _load_plugin_code(plugin_id, Path(manifest["_dir"])):
                    continue
                _loaded[plugin_id] = category
            await _reconcile_service_instances(plugin_id, db)
            continue
        if plugin_id in _loaded and not force:
            continue  # 幂等：已加载且仍启用，不重复导入（会顶掉行为处理器）
        if _load_plugin_code(plugin_id, Path(manifest["_dir"])):
            _loaded[plugin_id] = category
        defs = get_skill_defs(manifest)
        if defs:
            _register_plugin_skills(plugin_id, defs)

    # 重载后恢复运行态：之前在跑的服务实例继续跑
    for key, was_running in running.items():
        if not was_running:
            continue
        from app.services.infrastructure.plugin_registry import PluginRegistry

        plugin = PluginRegistry.get(key)
        if plugin is None:
            continue
        try:
            await plugin.start()
        except Exception as e:
            logger.warning(f"重扫后恢复服务失败: {key}: {e}")
