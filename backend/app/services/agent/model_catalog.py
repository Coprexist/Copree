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
    """这个账号能用的通道（与运行期同源：池 + 配置档），按可用顺序排列。

    以前这里自己拼「池 Key / 用户裸 Key / 全局默认」三档 —— 与运行期各写一份，
    用户 Key 被删掉之后还会指着一个不存在的通道。现在只读「配置」：
    有额度时池里那一条，加上这个人的 kind=llm 配置档；同地址只列一次。
    """
    from app.models.api_profile import UserApiProvider, UserApiProfile
    from app.models.user import User
    from app.services.agent.api_config_resolver import pool_candidate

    user = await db.get(User, user_id)
    channels: list[dict] = []
    seen: set[str] = set()

    def add(channel: dict) -> None:
        key = (channel.get("base_url") or "").rstrip("/")
        if not key or key in seen:
            return
        seen.add(key)
        channel["is_default"] = not channels
        channels.append(channel)

    pool = await pool_candidate(db, user)
    if pool.hit:
        add(channel_summary(pool.provider_info, pool.base_url,
                            models=list(pool.models),
                            default_model=pool.chat_model))

    stmt = (
        select(UserApiProfile, UserApiProvider)
        .join(UserApiProvider, UserApiProvider.id == UserApiProfile.provider_id)
        .where(UserApiProfile.user_id == user_id, UserApiProvider.kind == "llm")
        .order_by(UserApiProfile.is_default.desc(),
                  UserApiProfile.sort_order, UserApiProfile.id)
    )
    for profile, provider in (await db.execute(stmt)).all():
        add(channel_summary(
            {
                "name": provider.name,
                "system_layout": provider.system_layout,
                "system_layout_source": provider.system_layout_source,
                "thinking_supported": provider.thinking_supported,
                "model_options": [o for o in (provider.options or [])
                                  if isinstance(o, dict)],
            },
            provider.base_url or "",
            default_model=profile.primary_model or provider.default_primary,
        ))
    return channels
