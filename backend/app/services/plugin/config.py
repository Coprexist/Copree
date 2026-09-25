"""
插件配置读写 —— 唯一入口。

- 键与类型由插件声明的 config_schema 决定（声明即契约，不认 schema 外的键）
- **实例**：一个插件可以有多份配置（每个 QQ 机器人一份）。instance 是这个插件下的
  配置标识，不是插件标识——所以键、schema、加载逻辑都只有一份。
- 机密项（schema 里 secret: true）加密落库、读时解密；接口层只回"有没有值"
- 解密失败不抛 500：记日志 + 置空，与 user_credentials.user_api_key 同款处理
- 写入记审计日志（谁、哪个插件哪个实例、改了哪些键），**不记值**

为什么单独一层：插件作者只调 ServicePlugin.config()，不碰 DB、不碰加解密；
将来换存储或换加密算法，只改这里。
"""
from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.models.plugin import PluginConfig, PluginServiceState
from app.utils.crypto import decrypt_secret, encrypt_secret

logger = logging.getLogger(__name__)

# 实例 id：库表列宽 40，且要能安全放进注册表 key（plugin:instance）
INSTANCE_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,40}$")


def validate_instance(instance: str) -> str:
    value = (instance or "").strip()
    if value and not INSTANCE_RE.match(value):
        raise ValueError("实例名只能用字母、数字、下划线、点、短横线（1-40 个字符）")
    return value


def _is_secret(schema: dict[str, Any], key: str) -> bool:
    return bool((schema.get(key) or {}).get("secret"))


async def get_schema(plugin_id: str) -> dict[str, Any]:
    """插件声明的配置 schema（来自声明，不要求插件已加载——所以没启用也能看配置项）"""
    from app.services.plugin import api

    definition = api.get_service_def(plugin_id)
    return dict(definition.config_schema) if definition else {}


async def _fetch_rows(
    db: AsyncSession, plugin_id: str, instance: str
) -> dict[str, PluginConfig]:
    result = await db.execute(
        select(PluginConfig).where(
            PluginConfig.plugin_id == plugin_id,
            PluginConfig.instance == instance,
        )
    )
    return {row.key: row for row in result.scalars().all()}


async def get_config(
    plugin_id: str, instance: str = "", *, db: AsyncSession | None = None
) -> dict[str, Any]:
    """取某个实例的配置全量（机密已解密），含 schema 里声明但尚未填写的键（值为 None）。

    插件在 start() 里调它拿凭据；没配置的项返回 None，由插件自己判断是否可用。
    """
    instance = validate_instance(instance)
    schema = await get_schema(plugin_id)
    values: dict[str, Any] = {key: None for key in schema}

    async def _load(session: AsyncSession) -> None:
        for key, row in (await _fetch_rows(session, plugin_id, instance)).items():
            raw = row.value or ""
            if not row.is_secret:
                values[key] = raw or None
                continue
            if not raw:
                values[key] = None
                continue
            try:
                values[key] = decrypt_secret(raw)
            except Exception as e:
                # 不是"没填"，而是填了但解不开（密钥轮换/数据损坏）——必须留痕，否则排查时无从下手
                logger.warning(
                    f"插件 {plugin_id}[{instance}] 的配置项 {key} 解密失败"
                    f"（检查 ENCRYPTION_KEY 是否变更）: {e}"
                )
                values[key] = None

    if db is not None:
        await _load(db)
    else:
        async with async_session() as session:
            await _load(session)
    return values


def managed_values(schema: dict[str, Any]) -> dict[str, Any]:
    """schema 里 managed + default 的字段 → 平台应当写入的值

    managed 的语义是"平台知道、用户看不到也不填"（如 target_agent = 这个 AI 自己，
    或协议端也由平台提供时的地址与 token）。default 就是平台给的值。
    """
    return {
        key: spec["default"]
        for key, spec in (schema or {}).items()
        if spec.get("managed") and "default" in spec
    }


async def set_config(
    plugin_id: str,
    values: dict[str, Any],
    instance: str = "",
    *,
    actor: str | None = None,
    db: AsyncSession | None = None,
) -> list[str]:
    """部分更新某个实例的配置：未传的键不动，传空串即清除。返回被改动的键名。"""
    instance = validate_instance(instance)
    schema = await get_schema(plugin_id)
    if not schema:
        raise ValueError(f"插件 {plugin_id} 没有声明 config_schema，无法配置")
    # 托管字段直接覆盖：值由平台提供，客户端传什么都不算（不给伪造留口子）
    values = {**values, **managed_values(schema)}
    unknown = [k for k in values if k not in schema]
    if unknown:
        raise ValueError(f"未知配置项: {', '.join(unknown)}")

    async def _apply(session: AsyncSession) -> list[str]:
        rows = await _fetch_rows(session, plugin_id, instance)
        changed: list[str] = []
        for key, value in values.items():
            row = rows.get(key)
            text = "" if value is None else str(value)
            if text == "":
                if row is not None:
                    await session.delete(row)
                    changed.append(key)
                continue
            stored = encrypt_secret(text) if _is_secret(schema, key) else text
            if row is None:
                session.add(PluginConfig(
                    plugin_id=plugin_id, instance=instance, key=key, value=stored,
                    is_secret=_is_secret(schema, key),
                ))
                changed.append(key)
            elif row.value != stored:
                row.value = stored
                changed.append(key)
        await session.commit()
        return changed

    if db is not None:
        changed = await _apply(db)
    else:
        async with async_session() as session:
            changed = await _apply(session)
    if changed:
        # 只记"改了哪些键"，值本身可能是凭据，进日志就是泄密
        logger.info(
            f"插件配置已更新: {plugin_id}[{instance}] → {sorted(changed)}（操作者 {actor or 'system'}）"
        )
    return changed


async def mask_config(
    plugin_id: str, instance: str = "", *, db: AsyncSession | None = None
) -> dict[str, Any]:
    """给 API 的配置视图：schema + 非机密值 + 机密键"是否已填"。永不回显机密。"""
    instance = validate_instance(instance)
    schema = await get_schema(plugin_id)
    values: dict[str, Any] = {}
    secrets: dict[str, bool] = {}

    async def _load(session: AsyncSession) -> None:
        for key, row in (await _fetch_rows(session, plugin_id, instance)).items():
            if row.is_secret:
                secrets[key] = bool(row.value)
            else:
                values[key] = row.value or ""

    if db is not None:
        await _load(db)
    else:
        async with async_session() as session:
            await _load(session)
    # schema 里声明了但没填的机密项也要回 has_value=false，前端才能把表单画全
    for key in schema:
        if _is_secret(schema, key):
            secrets.setdefault(key, False)
    return {"instance": instance, "schema": schema, "values": values, "secrets": secrets}


async def list_instances(plugin_id: str, *, db: AsyncSession | None = None) -> list[str]:
    """某个插件已经配置过的实例 id（单实例插件固定只有 ""）"""
    from app.services.plugin import api

    definition = api.get_service_def(plugin_id)
    if definition is not None and not definition.multi_instance:
        return [""]

    async def _load(session: AsyncSession) -> list[str]:
        rows = (await session.execute(
            select(PluginConfig.instance).where(PluginConfig.plugin_id == plugin_id).distinct()
        )).scalars().all()
        return sorted(r for r in rows if r)

    if db is not None:
        return await _load(db)
    async with async_session() as session:
        return await _load(session)


# 用户为自己的 AI 建的实例（agent-<id>）归 AI 的所有者管，不归管理台那份"列表即真相"管：
# 管理员重扫/保存插件配置时若把它们当成"没提交 = 删除"，用户刚配好的通道就会凭空消失。
OWNER_SCOPED_PREFIX = "agent-"


def is_owner_scoped(instance: str) -> bool:
    return instance.startswith(OWNER_SCOPED_PREFIX)


async def replace_instances(
    plugin_id: str,
    instances: list[dict[str, Any]],
    *,
    actor: str | None = None,
    db: AsyncSession | None = None,
) -> dict[str, list[str]]:
    """列表即真相：提交的实例留下/更新，没提交的实例删除。

    只改配置，不动运行时——删掉的实例由调用方触发一次 reconcile（skill_bridge）来停掉，
    这样"谁负责启停"仍然只有一处。
    """
    schema = await get_schema(plugin_id)
    if not schema:
        raise ValueError(f"插件 {plugin_id} 没有声明 config_schema，无法配置")

    incoming: list[tuple[str, dict[str, Any]]] = []
    for item in instances:
        instance = validate_instance(str(item.get("instance") or ""))
        values = item.get("values") or {}
        if not isinstance(values, dict):
            raise ValueError("values 必须是对象")
        incoming.append((instance, values))
    ids = [i for i, _ in incoming]
    if len(set(ids)) != len(ids):
        raise ValueError("实例名重复")

    async def _apply(session: AsyncSession) -> dict[str, list[str]]:
        existing = set((await session.execute(
            select(PluginConfig.instance).where(PluginConfig.plugin_id == plugin_id).distinct()
        )).scalars().all())
        existing.discard("")
        incoming_ids = {i for i in ids if i}
        # 只删管理台自己管的实例：用户给自己 AI 建的通道不在这次"列表即真相"的范围内
        deleted = sorted(i for i in (existing - incoming_ids) if not is_owner_scoped(i))
        if deleted:
            await session.execute(
                delete(PluginConfig).where(
                    PluginConfig.plugin_id == plugin_id,
                    PluginConfig.instance.in_(deleted),
                )
            )
            await session.execute(
                delete(PluginServiceState).where(
                    PluginServiceState.plugin_id == plugin_id,
                    PluginServiceState.instance.in_(deleted),
                )
            )
            await session.commit()
        created = sorted(incoming_ids - existing)
        return {"created": created, "deleted": deleted, "updated": sorted(incoming_ids & existing)}

    if db is not None:
        summary = await _apply(db)
    else:
        async with async_session() as session:
            summary = await _apply(session)

    # 先删除、再逐实例写入（未传的键不动 → 前端不用把机密原值带回来）
    for instance, values in incoming:
        if values:
            await set_config(plugin_id, values, instance, actor=actor, db=db)
    if any(summary.values()):
        logger.info(f"插件实例已更新: {plugin_id} → {summary}（操作者 {actor or 'system'}）")
    return summary


# ── 服务插件的期望运行状态（按实例）─────────────────────────────
async def desired_states(*, db: AsyncSession | None = None) -> dict[tuple[str, str], bool]:
    """{(plugin_id, instance): desired_running}，供 bootstrap 恢复运行态"""
    async def _load(session: AsyncSession) -> dict[tuple[str, str], bool]:
        rows = (await session.execute(select(PluginServiceState))).scalars().all()
        return {(r.plugin_id, r.instance or ""): bool(r.desired_running) for r in rows}

    if db is not None:
        return await _load(db)
    async with async_session() as session:
        return await _load(session)


async def set_desired_running(
    plugin_id: str, instance: str, running: bool, db: AsyncSession
) -> None:
    instance = validate_instance(instance)
    row = await db.get(PluginServiceState, {"plugin_id": plugin_id, "instance": instance})
    if row is None:
        db.add(PluginServiceState(plugin_id=plugin_id, instance=instance, desired_running=running))
    elif row.desired_running != running:
        row.desired_running = running
    else:
        return
    await db.commit()
