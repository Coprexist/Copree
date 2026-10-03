"""把库里存过的旧模型名平升成现名（单一来源：provider_presets.MODEL_ALIASES）

为什么放在启动路径上、而不是写一份 Alembic 迁移：模型改名是厂商侧的常态（一年好几次），
而名字散落在几张配置表里。每次改名都补一份迁移，等于把这份"名字资产"劈成两半——现役清单在
provider_presets，历史却埋在 migrations 里。这里让清单当唯一真相：启动时按别名表把旧名就地改掉，
别的部署者拉到新代码就自动迁移，重复执行也没有副作用。

只碰配置列，不碰用量与对话日志：那些是历史事实，"当时用的哪个模型"不该被改写。
"""
from __future__ import annotations

import logging

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


def _targets():
    """存模型名的配置列：(模型, 列名, 额外条件)

    导入放函数里：启动早期导入模型会连带拉起重的一层依赖。

    Agent 的两列要带条件——自带 base_url 的 AI 走的是别人家的中转，那边的名字由对方定，
    不能按官方改名去动它（deepseek-v4-flash 在中转站很可能才是唯一认得的写法）。
    """
    from sqlalchemy import or_

    from app.models.agent import Agent
    from app.models.user import User
    from app.models.world import GroupAssistant, WorldAI

    official_api = or_(
        Agent.api_base_url.is_(None),
        Agent.api_base_url == "",
        Agent.api_base_url.ilike("%deepseek.com%"),
    )
    return (
        (Agent, "chat_model", official_api),
        (Agent, "work_model", official_api),
        (User, "global_chat_model", None),
        (User, "global_work_model", None),
        (WorldAI, "model", None),
        (GroupAssistant, "model", None),
    )


async def upgrade_provider_config(db: AsyncSession) -> int:
    """供应商配置（system_settings.provider_config 那份 JSON）里的旧模型名同样平升，返回改动处数

    单独一步的原因：这份 JSON 是"提供商"在库里的副本，管理台的预设卡片、模型下拉、
    「（覆盖）」输入框都直接读它——只改代码里的清单，页面还是旧的。
    """
    import json

    from sqlalchemy import select

    from app.models.system_settings import SystemSettings
    from app.services.agent.provider_presets import MODEL_ALIASES, PRESETS
    from app.utils.pure.provider_config import align_provider_models

    row = (await db.execute(select(SystemSettings).order_by(SystemSettings.id).limit(1))).scalars().first()
    raw = getattr(row, "provider_config", None) if row is not None else None
    if not raw:
        return 0
    items = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(items, list) or not items:
        return 0
    labels = {m["value"]: m["label"] for p in PRESETS.values() for m in p["models"]}
    presets = {
        p["key"]: {"chat_model": p["chat_model"], "work_model": p["work_model"], "models": p["models"]}
        for p in PRESETS.values()
    }
    new_items, changed = align_provider_models(
        items, presets=presets, aliases=MODEL_ALIASES, labels=labels,
    )
    if changed:
        # 按读到的类型回写：列可能是 JSON，也可能是存 JSON 文本的字符串列
        row.provider_config = json.dumps(new_items, ensure_ascii=False) if isinstance(raw, str) else new_items
    return changed


async def upgrade_stored_model_names(db: AsyncSession) -> dict[str, int]:
    """按别名表平升库里的旧模型名，返回 {表.列: 改动行数}（没有改动就是空字典）

    逐个别名一条 UPDATE：别名表只有几行，一条 SQL 对应一次改名，执行计划简单，行数也便于核对。
    不在这里 commit —— 交给调用方，免得替别人的事务收尾。
    """
    from app.services.agent.provider_presets import MODEL_ALIASES

    changed: dict[str, int] = {}
    for old_name, new_name in MODEL_ALIASES.items():
        for model, column, extra in _targets():
            conditions = [getattr(model, column) == old_name]
            if extra is not None:
                conditions.append(extra)
            result = await db.execute(update(model).where(*conditions).values({column: new_name}))
            if result.rowcount:
                key = f"{model.__tablename__}.{column}"
                changed[key] = changed.get(key, 0) + int(result.rowcount)
    config_changed = await upgrade_provider_config(db)
    if config_changed:
        changed["system_settings.provider_config"] = config_changed
    return changed
