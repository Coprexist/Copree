""""已登记的出站地址" —— 唯一入口。

用户能填自己的局域网 LLM，但不能拿服务器扫内网。这条线的落点是：

    私网地址只有在**已经被保存过**时才允许去请求
    （平台预设 / 平台 provider_config / 该用户自己的 user、agent、world 配置）

于是想探内网就必须"每个地址先保存一次再测一次"，那是手点而不是扫描，
而且每次保存都是正常的配置写（有记录）。公网地址不受任何限制——
用户测 DeepSeek/OpenAI 那些的体验一个字节都没变。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.utils.pure.url_guard import host_port, is_private_target_async


async def _platform_urls(db: AsyncSession) -> list[str]:
    """平台自己配的地址：内置预设 + system_settings.provider_config。"""
    urls: list[str] = []
    try:
        from app.utils.pure.provider_presets import get_all_presets
        urls += [p.get("base_url") or "" for p in get_all_presets()]
    except Exception:
        pass
    try:
        from app.services.infrastructure.system_settings_service import get_providers
        urls += [(p or {}).get("base_url") or "" for p in await get_providers(db)]
    except Exception:
        pass
    return urls


async def _column_values(db: AsyncSession, query) -> list[str]:
    """查一列地址；某张表结构对不上不该让「测试连接」整个不可用——少一个登记来源而已。"""
    try:
        rows = await db.execute(query)
    except Exception:
        return []
    return [r[0] for r in rows.all() if r[0]]


async def _user_urls(db: AsyncSession, user_id: int) -> list[str]:
    """该用户自己的配置里已经保存过的地址（账号 / AI / 群助手 / API 厂商）。

    **厂商表必须在里面**：用户自己填的 llama-server 地址只存在 user_api_providers，
    漏了它这条地址就永远不算「已保存过」——保存时判不了 system 布局，测连接也探不了，
    表现是自建本地服务只能用猜的（见 services/agent/channel_layout.py）。

    这里不查 worlds：世界表没有地址列（历史上那个分支一直被 except 吞掉，从没生效过）。
    """
    from app.models.agent import Agent
    from app.models.api_profile import UserApiProvider
    from app.models.group import Group
    from app.models.user import User
    from app.models.world import GroupAssistant

    urls: list[str] = []
    # 直接按「人」归属的：账号、AI、API 厂商（厂商表的列名是 base_url，不是 api_base_url）
    for cond, column in (
        (User.id == user_id, User.api_base_url),
        (Agent.owner_id == user_id, Agent.api_base_url),
        (UserApiProvider.user_id == user_id, UserApiProvider.base_url),
    ):
        urls += await _column_values(db, select(column).where(cond, column.isnot(None)))

    # 群助手挂在群上：先按群主缩小到自己的群
    owned_groups = select(Group.id).where(Group.owner_id == user_id)
    urls += await _column_values(db, select(GroupAssistant.api_base_url).where(
        GroupAssistant.group_id.in_(owned_groups),
        GroupAssistant.api_base_url.isnot(None),
    ))
    return urls


async def _private_only(urls: list[str]) -> set[str]:
    """只留私网地址的 host:port；解析域名走 async 版，别把事件循环停在 DNS 上。"""
    hosts: set[str] = set()
    for url in urls:
        if url and await is_private_target_async(url):
            hosts.add(host_port(url))
    return hosts


async def saved_private_hosts(db: AsyncSession, user_id: int) -> set[str]:
    """该用户被允许请求的**私网** host:port 集合（公网不需要登记，故不进集合）。"""
    return await _private_only(await _platform_urls(db) + await _user_urls(db, user_id))
