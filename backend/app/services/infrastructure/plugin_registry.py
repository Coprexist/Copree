"""
服务插件注册中心 — 管理所有系统服务插件的注册、状态查询和启停

两种来源，同一个注册表：
- 内置服务插件：模块导入时自注册（如 browser），实例为空串
- 目录服务插件：plugins/<id>/ 的 plugin.py 用 @service 声明，加载器按 DB 里的实例列表
  逐个实例化注册（一个插件可以有多份配置 = 多个实例）

注册表 key：单实例是 plugin_id；多实例是 "plugin_id:instance"。
实例是"同一个插件的不同配置"，不是一个新插件——所以 key 里必须带上插件 id。
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

KEY_SEP = ":"


def registry_key(plugin_id: str, instance: str = "") -> str:
    """注册表 key：单实例就是插件 id，多实例带实例后缀（全局唯一）"""
    return f"{plugin_id}{KEY_SEP}{instance}" if instance else plugin_id


def split_key(key: str) -> tuple[str, str]:
    """注册表 key → (plugin_id, instance)"""
    if KEY_SEP in key:
        plugin_id, _, instance = key.partition(KEY_SEP)
        return plugin_id, instance
    return key, ""


class ServicePlugin:
    """服务插件基类 — 每个系统服务一个子类"""

    id: str = ""
    name: str = ""
    description: str = ""
    category: str = "service"
    # 实例 id：多实例插件用（如机器人别名）；单实例为空串
    instance: str = ""
    # 是否多实例（由 @service(multi_instance=True) 声明后抄到实例上，供状态视图使用）
    multi_instance: bool = False
    # 配置项 JSON Schema（前端据此生成表单；secret: true 的项加密落库）
    config_schema: dict[str, Any] = {}
    # 来源插件 id：目录插件 = 插件 id，内置插件 = None。
    # 卸载时据此判断"该不该回收"，避免误删同名内置插件。
    owner: str | None = None

    @property
    def key(self) -> str:
        """注册表 key（单实例 = id）"""
        return registry_key(self.id, self.instance)

    def display_name(self) -> str:
        return f"{self.name} · {self.instance}" if self.instance else self.name

    async def get_status(self) -> dict[str, Any]:
        """返回当前运行状态"""
        return {"installed": False, "running": False}

    @classmethod
    async def hosted_status(cls) -> dict[str, Any] | None:
        """平台托管这份服务时的状态；没有可托管的运行时状态就返回 None

        get_status() 得先有实例才能问，而"平台自带协议端的登录"发生在实例配置之前——
        卡片那时还没有实例，所以给平台留一个类方法问这一件事。
        """
        return None

    async def start(self) -> bool:
        """启动服务"""
        raise NotImplementedError(f"{self.id} 未实现 start()")

    async def stop(self) -> bool:
        """停止服务"""
        raise NotImplementedError(f"{self.id} 未实现 stop()")

    async def config(self) -> dict[str, Any]:
        """读取本实例的配置（机密已解密）。

        插件只调这里，不碰 DB、不碰加解密——存储与算法变了也不用改插件。
        """
        from app.services.plugin.config import get_config

        return await get_config(self.id, self.instance)


class PluginRegistry:
    """服务插件注册表 — 单例（按注册表 key 索引）"""

    _plugins: dict[str, ServicePlugin] = {}

    @classmethod
    def register(cls, plugin: ServicePlugin) -> None:
        """注册一个服务插件实例"""
        key = plugin.key
        if key in cls._plugins:
            logger.warning(f"插件实例 {key} 重复注册，已覆盖")
        cls._plugins[key] = plugin
        logger.info(f"服务插件已注册: {key} ({plugin.name})")

    @classmethod
    def unregister(cls, key: str) -> bool:
        """摘掉一个服务插件实例（调用方负责先 stop）"""
        if cls._plugins.pop(key, None) is None:
            return False
        logger.info(f"服务插件已回收: {key}")
        return True

    @classmethod
    def get(cls, key: str) -> ServicePlugin | None:
        return cls._plugins.get(key)

    @classmethod
    def keys_of(cls, plugin_id: str) -> list[str]:
        """某个目录插件的全部实例 key"""
        return [k for k, p in cls._plugins.items() if p.owner == plugin_id]

    @classmethod
    def get_all(cls) -> list[ServicePlugin]:
        return list(cls._plugins.values())

    @classmethod
    async def get_status_all(cls) -> list[dict[str, Any]]:
        """获取所有实例的状态（供 API 返回）"""
        results = []
        for plugin in cls._plugins.values():
            try:
                status = await plugin.get_status()
            except Exception as e:
                logger.warning(f"获取插件 {plugin.key} 状态失败: {e}")
                status = {"installed": False, "running": False}
            results.append({
                "id": plugin.key,
                "plugin_id": plugin.id,
                "instance": plugin.instance,
                "name": plugin.display_name(),
                "description": plugin.description,
                "category": plugin.category,
                # owner 一并回给前端：目录插件（可配置）与内置插件（无配置）由此区分
                "owner": plugin.owner,
                "config_schema": plugin.config_schema or {},
                **status,
            })
        return results
