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
        from app.services.agent.provider_presets import get_all_presets
        urls += [p.get("base_url") or "" for p in get_all_presets()]
    except Exception:
        pass
    try:
        from app.services.infrastructure.system_settings_service import get_providers
        urls += [(p or {}).get("base_url") or "" for p in await get_providers(db)]
    except Exception:
        pass
    return urls


async def _user_urls(db: AsyncSession, user_id: int) -> list[str]:
    """该用户自己的 user / agent / world 配置里已经保存过的地址。"""
    from app.models.agent import Agent
    from app.models.user import User
    from app.models.world import World

    urls: list[str] = []
    for model, cond in (
        (User, User.id == user_id),
        (Agent, Agent.owner_id == user_id),
        (World, World.owner_id == user_id),
    ):
        try:
            rows = await db.execute(select(model.api_base_url).where(cond, model.api_base_url.isnot(None)))
            urls += [r[0] for r in rows.all() if r[0]]
        except Exception:
            # 某个表结构对不上不该让"测试连接"整个不可用：少一个登记来源而已
            continue
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
