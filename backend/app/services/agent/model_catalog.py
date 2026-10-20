"""创建 AI 时能选哪些模型 —— 按「这个账号真能调用的通道」给，不按「平台登记过哪些供应商」给。

运行期走哪条通道由 ai/executor._get_api_config 决定（池 Key / 本人 Key / 全局默认），
模型名必须是那条通道认得的：2026-10-19 用户从创建页选了 xAI 的 grok-4.6，凭证却落到
DeepSeek 的池 Key 上，每轮 400，用户那边什么都看不到。

所以下拉框不该把十四家供应商的模型平铺——这里把运行期会用到的那几条通道摊开，
每条通道自己的模型清单就是他能选的模型；同一个地址只算一条（池 Key 与全局默认常是同一家）。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


def model_options(provider: dict | None, ids: list | None) -> list[dict]:
    """通道的模型清单：池 Key 自己记了 id 就按它排，展示名能对上预设的用预设的。"""
    provider = provider or {}
    labels = {
        m["value"]: m.get("label") or m["value"]
        for m in (provider.get("model_options") or [])
        if isinstance(m, dict) and m.get("value")
    }
    order = [m for m in (ids or []) if isinstance(m, str) and m]
    return [{"value": v, "label": labels.get(v, v)} for v in (order or list(labels))]


def channel_summary(provider: dict | None, base_url: str, *, models: list | None = None,
                    default_model: str | None = None, is_default: bool = False) -> dict:
    """一条通道的展示形态（前端 ProviderInfo 的形状）。"""
    from app.utils.pure.provider_config import channel_provider_info

    provider = provider or {}
    info = channel_provider_info(provider, base_url, models=models, default_model=default_model)
    return {
        "name": provider.get("name") or provider.get("provider") or base_url,
        "provider": provider.get("provider", ""),
        "base_url": info["base_url"],
        "api_key_url": provider.get("api_key_url", ""),
        "thinking_supported": info["thinking_supported"],
        "is_default": is_default,
        "chat_model": info["default_model"],
        "work_model": info["default_model"],
        "models": model_options(provider, info["models"]),
    }


async def selectable_channels(db: AsyncSession, user_id: int) -> list[dict]:
    """这个账号能用的通道，按运行期的优先顺序排列（与 _get_api_config 同一套来源）。

    - 有额度 → 平台池 Key（它自己记着模型清单与默认模型）
    - 配了本人 Key → 按它的 base_url 找供应商预设，拿那条通道认得的模型
    - 最后补一条全局默认，保证列表永远不空
    """
    from app.config import settings
    from app.models.user import User
    from app.services.infrastructure.system_settings_service import (
        get_provider_by_base_url, get_provider_for_pool_key,
    )

    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    channels: list[dict] = []
    seen: set[str] = set()

    def add(channel: dict) -> None:
        key = (channel.get("base_url") or "").rstrip("/")
        if not key or key in seen:
            return
        seen.add(key)
        channel["is_default"] = not channels
        channels.append(channel)

    credit = max(0, getattr(user, "platform_gifted_credit", 0) or 0) + (getattr(user, "api_credit", 0) or 0)
    if user is not None and credit > 0:
        from app.services.infrastructure.quota_service import find_best_pool_key

        pool_key = await find_best_pool_key(db, user.id)
        if pool_key is not None:
            base = pool_key.api_base_url or settings.deepseek_base_url
            provider = await get_provider_for_pool_key(db, pool_key)
            add(channel_summary(provider, base,
                                models=getattr(pool_key, "models", None),
                                default_model=getattr(pool_key, "default_model", None)))

    if user is not None and user.api_key_encrypted:
        base = user.api_base_url or settings.deepseek_base_url
        add(channel_summary(await get_provider_by_base_url(db, base), base,
                            default_model=getattr(user, "global_chat_model", None)))

    base = settings.deepseek_base_url
    add(channel_summary(await get_provider_by_base_url(db, base), base))
    return channels
