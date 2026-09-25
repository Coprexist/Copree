"""每个 AI 的通道（目前只有 QQ）— 归属、实例命名、配置读写、配对视图

实例 id 约定 agent-<agentId>：
- 一个 AI 一个通道，天然不冲突；归属直接从 agents 表读，不用给 plugin_configs 加"归属人"列
- 管理员那份"列表即真相"的配置接口对 agent- 前缀有护栏（见 plugin/config.py），
  所以管理员重扫/保存插件配置时不会把用户给自己的 AI 建的通道删掉
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.plugin import config as plugin_config
from app.services.plugin import pairing
from app.services.plugin.runtime_control import StartFailed, UnknownInstance
from app.services.plugin import runtime_control

logger = logging.getLogger(__name__)

QQ_PLUGIN_ID = "qq-channel"
INSTANCE_PREFIX = "agent-"


def instance_of(agent_id: int) -> str:
    return INSTANCE_PREFIX + str(agent_id)


def agent_id_of(instance: str) -> int | None:
    if not instance.startswith(INSTANCE_PREFIX):
        return None
    tail = instance[len(INSTANCE_PREFIX):]
    return int(tail) if tail.isdigit() else None


class NotOwned(PermissionError):
    """不是你的 AI——通道属于 AI 的所有者。

    这里刻意不给管理员开后门（和 agents 路由的 is_owner 口径不同）：通道配置里存的是
    用户自己的机器人凭据，管理员排障走控制台的插件配置接口就够了，不需要从这条路径碰别人的凭据。
    """


async def owned_agent(db: AsyncSession, agent_id: int, user_id: int):
    from app.models.agent import Agent
    from fastapi import HTTPException

    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(404, "AI 不存在")
    if int(agent.owner_id) != int(user_id):
        raise NotOwned("这不是你的 AI")
    return agent


async def group_options(db: AsyncSession, agent_id: int, user_id: int) -> list[dict[str, Any]]:
    """可以把这个 AI 接进去的 Copree 群：**你管的** 且 **它已经在里面的**。

    两个条件缺一不可：只列你管理的群，别人不会因为你的机器人被塞进他们的群；
    只列它已经是成员的群，否则 QQ 消息落进去、它却不在群里，等于往别人群里灌消息。
    （约定：AI 成员在 group_members 里用 agent.user_id 当 member_id。）
    """
    from sqlalchemy import or_, select

    from app.models.agent import Agent
    from app.models.group import Group, GroupMember

    agent = await db.get(Agent, agent_id)
    if agent is None:
        return []
    groups_i_manage = select(GroupMember.group_id).where(
        GroupMember.member_type == "human",
        GroupMember.member_id == user_id,
        GroupMember.role.in_(("owner", "admin")),
    )
    groups_i_own = select(Group.id).where(Group.owner_type == "human", Group.owner_id == user_id)
    groups_with_this_ai = select(GroupMember.group_id).where(
        GroupMember.member_type == "ai", GroupMember.member_id == agent.user_id,
    )
    stmt = (
        select(Group.id, Group.name)
        .where(Group.id.in_(groups_with_this_ai))
        .where(or_(Group.id.in_(groups_i_manage), Group.id.in_(groups_i_own)))
        .order_by(Group.id.desc())
    )
    return [{"id": int(r[0]), "name": r[1]} for r in (await db.execute(stmt)).all()]


async def create_landing_group(db: AsyncSession, *, agent_id: int, user_id: int, name: str) -> dict:
    """在 Copree 单独建一个群当 QQ 消息的落点：你是群主，这个 AI 是成员。

    为什么走 create_group：建群还要带群主成员行、并发上限等一串约定，
    那些都住在 app/chat/gm.py 一处，不在这里再写一份。
    """
    from app.chat.gm import create_group
    from app.models.agent import Agent

    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise ValueError("AI 不存在")
    clean = (name or "").strip()[:60] or (str(agent.name) + " 的 QQ 群")
    group = await create_group(
        db, clean, "human", user_id, initial_members=[{"type": "ai", "id": agent.user_id}]
    )
    await db.commit()
    logger.info("为 AI #%s 建了 QQ 落点群 #%s（%s）", agent_id, group.id, clean)
    return {"id": int(group.id), "name": group.name}


async def _plugin_enabled(db: AsyncSession) -> bool:
    """通道要管理员在控制台把插件全局打开；关了就是"这个功能没开"，不是用户能自己绕过的开关"""
    from app.models.plugin import Plugin

    row = await db.get(Plugin, QQ_PLUGIN_ID)
    return bool(row and row.enabled)


def channel_kind() -> str:
    """这个通道作为外部身份的类别名：由插件的 manifest 声明（channel.kind），平台侧不另写常量"""
    from app.services.plugin import catalog

    return catalog.channel_kind(QQ_PLUGIN_ID)


async def view(db: AsyncSession, agent_id: int, user_id: int) -> dict[str, Any]:
    """通道视图：配置（机密只报"填没填"）+ 运行态 + 待批/已批的配对名单"""
    from app.services.infrastructure.plugin_registry import PluginRegistry, registry_key

    from app.services.plugin.skill_bridge import ensure_declared

    # 卡片要能画出表单，先确保插件的声明已加载（拿不到 schema 就画不出字段）
    ensure_declared(QQ_PLUGIN_ID)
    instance = instance_of(agent_id)
    masked = await plugin_config.mask_config(QQ_PLUGIN_ID, instance, db=db)
    schema = await plugin_config.get_schema(QQ_PLUGIN_ID)
    rows = await pairing.list_rows(db, kind=channel_kind(), owner_scope=instance)
    plugin = PluginRegistry.get(registry_key(QQ_PLUGIN_ID, instance))
    running = False
    detail: dict = {}
    if plugin is not None:
        try:
            detail = dict(await plugin.get_status() or {})
            running = bool(detail.pop("running", False))
            detail.pop("installed", None)
        except Exception as e:  # 插件自己报状态失败不该把整个卡片打挂
            detail = {"error": str(e)}
    # managed=True 的字段由平台自己填（如 target_agent = 这个 AI 自己），
    # 不列进"还缺什么"——用户看不到、也填不了的东西不该变成一条待办
    missing = [
        key for key, spec in (schema or {}).items()
        if spec.get("required") and not spec.get("managed") and not (
            masked["secrets"].get(key, False) if spec.get("secret") else bool(masked["values"].get(key))
        )
    ]
    # 已批准的那一位（QQ 侧一人一实例，卡片上只展示一个"当前放行的人"）
    owner_row = next((r for r in rows if r.status == pairing.APPROVED), None)
    return {
        "plugin_id": QQ_PLUGIN_ID,
        "instance": instance,
        "enabled": await _plugin_enabled(db),
        "schema": schema or {},
        "values": masked["values"],
        "secrets": masked["secrets"],
        "running": running,
        "detail": detail,
        "missing_required": missing,
        "configured": bool(masked["values"]) or any(masked["secrets"].values()),
        "owner": owner_row and {
            "openid": owner_row.origin,
            "nickname": owner_row.display_name,
            "approved_at": owner_row.approved_at,
        } or None,
        "pending": [
            {"id": r.id, "openid": r.origin, "nickname": r.display_name, "code": r.code, "created_at": r.created_at}
            for r in rows if r.status == pairing.PENDING
        ],
        "approved": [
            {"id": r.id, "openid": r.origin, "nickname": r.display_name, "approved_at": r.approved_at}
            for r in rows if r.status == pairing.APPROVED
        ],
        # 前端下拉用：能接的 Copree 群（你管的 + 这个 AI 已经在里面的）
        "group_options": await group_options(db, agent_id, user_id),
        "blocked": [
            {"id": r.id, "openid": r.origin, "nickname": r.display_name}
            for r in rows if r.status == pairing.BLOCKED
        ],
    }


async def save(
    db: AsyncSession, *, agent_id: int, user_id: int, values: dict, actor: str, target_agent_name: str
) -> dict[str, Any]:
    """保存通道配置并让它生效：写配置 → 建实例 → 启动。

    target_agent 不由用户填：入口在某个 AI 的页面上，它就是那个 AI ——
    让用户自己填名字，等于允许把机器人指到别人的 AI 上。
    """
    from app.services.infrastructure.plugin_registry import registry_key
    from app.services.plugin.skill_bridge import apply_skill_plugins, ensure_declared

    if not await _plugin_enabled(db):
        raise PermissionError("管理员还没有开放 QQ 通道")
    ensure_declared(QQ_PLUGIN_ID)
    # 允许部分保存（只交白名单、只改策略都算）：缺什么由 missing_required 提示、
    # 启动时插件自己会报"未配置: xxx"。以前这里要求"每次都得带凭据"，
    # 配合 set_config 的"空串=清除"语义，会把已存的机密抹掉。

    payload = dict(values)
    payload["target_agent"] = target_agent_name

    # 接了群就必须是"你管的、且这个 AI 已经在里面的"群：否则 QQ 的消息会落进别人的群
    raw_group = str(payload.get("copree_group_id") or "").strip()
    if raw_group:
        allowed = {int(g["id"]) for g in await group_options(db, agent_id, user_id)}
        if not raw_group.isdigit() or int(raw_group) not in allowed:
            raise ValueError("这个群不能接：只能选你管理、并且这个 AI 已经在里面的 Copree 群")

    instance = instance_of(agent_id)
    await plugin_config.set_config(QQ_PLUGIN_ID, payload, instance=instance, actor=actor, db=db)
    # 实例要先在注册表里存在才能启停：reconcile 是幂等的，多跑一次没关系
    await apply_skill_plugins(db)

    warning = ""
    running = False
    try:
        # 配置改了要重新读：先停再起（运行中直接 start 只会回一句"已在运行"，改动不生效）
        await runtime_control.stop_instance(db, registry_key(QQ_PLUGIN_ID, instance))
    except UnknownInstance:
        pass
    try:
        result = await runtime_control.start_instance(db, registry_key(QQ_PLUGIN_ID, instance))
        running, warning = bool(result.get("running")), ""
    except UnknownInstance:
        warning = "配置已保存，但实例还没起来（请确认管理员已开放 QQ 通道）"
    except StartFailed as e:
        warning = "配置已保存，但" + str(e)
    return {"running": running, "warning": warning}


async def start(db: AsyncSession, agent_id: int) -> dict[str, Any]:
    from app.services.infrastructure.plugin_registry import registry_key

    if not await _plugin_enabled(db):
        raise PermissionError("管理员还没有开放 QQ 通道")
    return await runtime_control.start_instance(db, registry_key(QQ_PLUGIN_ID, instance_of(agent_id)))


async def stop(db: AsyncSession, agent_id: int) -> dict[str, Any]:
    from app.services.infrastructure.plugin_registry import registry_key

    return await runtime_control.stop_instance(db, registry_key(QQ_PLUGIN_ID, instance_of(agent_id)))


async def approve(db: AsyncSession, agent_id: int, *, pairing_id: int | None = None, code: str | None = None) -> dict:
    row = await pairing.approve(
        db, kind=channel_kind(), owner_scope=instance_of(agent_id), pairing_id=pairing_id, code=code
    )
    return {"openid": row.origin, "nickname": row.display_name, "status": row.status}
