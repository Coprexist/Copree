"""预设 → "已配置"的同步：标了 auto_config 的预设，发布即自带一条配置项

为什么要有这一层：预设清单住在代码里（provider_presets），而"已配置"是库里的副本
（system_settings.provider_config）。版本更新加了一家主流厂商，别人升级后还得自己去点一遍，
才算用上——这一步把它补齐。

两条边界：
- 只补标了 `auto_config` 的：次主流与聚合平台不硬塞，使用者点一下才成为配置项（列表也不至于太长）。
- **删过的不再补**：删除时把键记进 `runtime_config.dismissed_providers`——那是"我不要这家"的账，
  少了它，管理员每次删完都会被下一次启动加回来。

写 provider_config 一律交回给 JSON 列自己序列化（赋 list）；手动 json.dumps 会让 JSONB 存成一个
字符串（双重编码），"已配置"因此读不出来。
"""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

DISMISSED_KEY = "dismissed_providers"


def _runtime_config(row: Any) -> dict:
    """读 runtime_config（容错：历史数据可能是被双重编码过的字符串）"""
    raw = getattr(row, "runtime_config", None)
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return {}
    return dict(raw) if isinstance(raw, dict) else {}


async def ensure_auto_providers(db: AsyncSession) -> list[str]:
    """把标了 auto_config 的预设补成配置项，返回本次新增的键"""
    from sqlalchemy import select

    from app.models.system_settings import SystemSettings
    from app.services.agent.provider_presets import PRESETS
    from app.services.infrastructure.system_settings_service import get_providers
    from app.utils.pure.provider_config import build_provider_config, upsert_provider

    row = (await db.execute(
        select(SystemSettings).order_by(SystemSettings.id).limit(1)
    )).scalars().first()
    if row is None:
        return []
    providers = await get_providers(db)
    have = {str(p.get("provider") or "") for p in providers}
    dismissed = {str(k) for k in (_runtime_config(row).get(DISMISSED_KEY) or [])}
    added: list[str] = []
    for preset in PRESETS.values():
        key = str(preset["key"])
        if not preset.get("auto_config") or key in have or key in dismissed:
            continue
        providers = upsert_provider(providers, build_provider_config(
            name=preset["label"], provider_key=key, base_url=preset["base_url"],
            chat_model=preset["chat_model"], work_model=preset["work_model"],
            embedding_model=preset["embedding_model"], model_options=preset["models"],
            thinking_supported=preset["thinking_supported"], is_default=False,
        ))
        added.append(key)
    if added:
        row.provider_config = providers        # list 交给 JSON 列，别 dumps
        await db.commit()
        logger.info(f"[OK] 自动加入的配置项: {', '.join(added)}")
    return added


async def dismiss_provider(db: AsyncSession, provider_key: str) -> None:
    """记下"这家我不要"：把键写进 runtime_config.dismissed_providers（幂等，不 commit）"""
    from sqlalchemy import select

    from app.models.system_settings import SystemSettings

    if not provider_key:
        return
    row = (await db.execute(
        select(SystemSettings).order_by(SystemSettings.id).limit(1)
    )).scalars().first()
    if row is None:
        return
    runtime = _runtime_config(row)
    keys = {str(k) for k in (runtime.get(DISMISSED_KEY) or [])}
    if provider_key in keys:
        return
    keys.add(provider_key)
    runtime[DISMISSED_KEY] = sorted(keys)
    row.runtime_config = runtime
