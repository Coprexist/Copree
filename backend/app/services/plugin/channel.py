"""每个 AI 的通道 — 归属、实例命名、配置读写、配对视图

通道是由**插件自己声明的**（manifest 的 channel 块，见 catalog.channels()）：
平台侧不维护"有哪些通道"的常量表，第三方通道插件装上就出现、卸载就消失。

实例 id 约定 agent-<agentId>：
- 一个 AI 一条通道（同一插件下），天然不冲突；归属直接从 agents 表读，
  不用给 plugin_configs 加"归属人"列
- 管理员那份"列表即真相"的配置接口对 agent- 前缀有护栏（见 plugin/config.py），
  所以管理员重扫/保存插件配置时不会把用户给自己的 AI 建的通道删掉
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.plugin import catalog
from app.services.plugin import config as plugin_config
from app.services.plugin import pairing
from app.services.plugin import runtime_control
from app.services.plugin.runtime_control import StartFailed, UnknownInstance

logger = logging.getLogger(__name__)

INSTANCE_PREFIX = "agent-"


class UnknownChannel(LookupError):
    """没有这个通道——插件没声明 channel 块，或插件根本不存在"""


class NotOwned(PermissionError):
    """不是你的 AI——通道属于 AI 的所有者。

    这里刻意不给管理员开后门（和 agents 路由的 is_owner 口径不同）：通道配置里存的是
    用户自己的机器人凭据，管理员排障走控制台的插件配置接口就够了，不需要从这条路径碰别人的凭据。
    """


class NoSelfTest(NotImplementedError):
    """这条通道没有可自测的出口（插件没实现 self_test）"""


def instance_of(agent_id: int) -> str:
    return INSTANCE_PREFIX + str(agent_id)


def agent_id_of(instance: str) -> int | None:
    if not instance.startswith(INSTANCE_PREFIX):
        return None
    tail = instance[len(INSTANCE_PREFIX):]
    return int(tail) if tail.isdigit() else None


def declared(plugin_id: str) -> dict[str, Any]:
    """取一个已声明的通道；没声明就是 404 的料，不在这里猜"""
    found = catalog.channel_plugin(plugin_id)
    if found is None:
        raise UnknownChannel(f"没有这个通道: {plugin_id}")
    return found


def all_declared() -> list[dict[str, Any]]:
    return catalog.channels()


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
    """在 Copree 单独建一个群当外部消息的落点：你是群主，这个 AI 是成员。

    为什么走 create_group：建群还要带群主成员行、并发上限等一串约定，
    那些都住在 app/chat/gm.py 一处，不在这里再写一份。
    """
    from app.chat.gm import create_group
    from app.models.agent import Agent

    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise ValueError("AI 不存在")
    clean = (name or "").strip()[:60] or (str(agent.name) + " 的群")
    group = await create_group(
        db, clean, "human", user_id, initial_members=[{"type": "ai", "id": agent.user_id}]
    )
    await db.commit()
    logger.info("为 AI #%s 建了落点群 #%s（%s）", agent_id, group.id, clean)
    return {"id": int(group.id), "name": group.name}


async def group_brief(db: AsyncSession, group_id: int) -> str:
    """这个群经不经过外部通道、那条通道有什么规矩 —— 给 AI 的一段话（没有通道就返回空串）

    为什么由平台注入，而不是让 AI 自己猜：消息从 QQ 来这件事背后有一串接口约束
    （腾讯 2025-04-21 起下线了主动推送），不说清它就会答应"我待会儿在群里提醒你"，
    然后什么都发不出去。

    「@其他成员」那条规矩**随群的实际模式变**（全量开着时正文是完整的）：模式由插件观测
    事件类型得到，这里问活着的实例；问不到就两句话都讲，不猜——猜错了 AI 会当事实用。
    """
    from sqlalchemy import select

    from app.models.plugin import PluginConfig

    rows = (await db.execute(
        select(PluginConfig.plugin_id, PluginConfig.instance).where(
            PluginConfig.key == "copree_group_id", PluginConfig.value == str(group_id)
        )
    )).all()
    found_channels: list[dict[str, Any]] = []
    qq_modes: list[bool | None] = []
    for plugin_id, instance in rows:
        if agent_id_of(str(instance)) is None:
            continue                      # 不是"某个 AI 的通道"就不是这个群的出口
        found = catalog.channel_plugin(str(plugin_id))
        if not found:
            continue
        if found["kind"] == "qq":
            qq_modes.append(_live_full_mode(str(plugin_id), str(instance)))
        # 同一个插件有多个实例（多条通道）时，说明里只列一次
        if found["plugin_id"] not in [c["plugin_id"] for c in found_channels]:
            found_channels.append(found)
    if not found_channels:
        return ""
    kinds = [c["kind"] for c in found_channels]
    labels = "、".join(c["label"] for c in found_channels)
    lines = [
        "",
        "",
        "## 这个群接进了外部聊天软件（" + labels + "）",
        "- 群里你只能**被动回复**：别人 @ 你（或回复你）时才轮到你说话；不要承诺「我待会儿在群里发」「稍后提醒你」这类主动开口。",
    ]
    if "qq" in kinds:
        lines.append(
            "- 官方 QQ 机器人：腾讯自 2025-04-21 起下线了主动推送；被动回复的有效窗口是"
            "群 5 分钟、私聊 60 分钟，同一条消息最多回 5 次，超时就发不出去。"
        )
        lines.append(
            "- 官方机器人私聊**可以**主动发消息，但每天每个用户最多 2 条：留给要紧的提醒，别用来说废话。"
        )
        lines.append("- 你写的 Markdown 会尽量按富文本发（机器人没开通 Markdown 权限时平台会自动降级成纯文本）。")
        lines.append(_mention_rule_line(_brief_full_mode(qq_modes)))
        lines.append(
            "- **撤回不同步**：QQ 里别人撤回的消息我们收不到通知，你上下文里那条还在（就当它发生过）；"
            "反过来你在站内撤回（2 分钟内）我们会请 QQ 一起撤，超时就只有站内撤掉。"
        )
    if "qq-napcat" in kinds:
        lines.append(
            "- NapCat 通道用的是真 QQ 号（协议端）：没有上面那些接口窗口限制，但同样别刷屏，"
            "并且富文本能不能渲染取决于 QQ 客户端。"
        )
    return "\n".join(lines)


def _live_full_mode(plugin_id: str, instance: str) -> bool | None:
    """问活着的插件实例：这个群最近一次观测到的推送模式（拿不到就 None）

    为什么不落库：这是运行期观测（事件类型），不是配置；重启后第一条群消息就能重新观测到。
    给 AI 的**持久**记录在账本里（QQ 插件投的「通道变更」通知），不靠这里。
    """
    from app.services.infrastructure.plugin_registry import PluginRegistry, registry_key

    plugin = PluginRegistry.get(registry_key(plugin_id, instance))
    probe = getattr(plugin, "observed_full_mode", None)
    return probe() if callable(probe) else None


def _brief_full_mode(states: list[bool | None]) -> bool | None:
    """这个群到底开没开全量：所有相关通道都观测到同一种才敢下结论，否则算"不知道"

    混着说比说错强——说错了 AI 会拿它当事实去跟人对话。
    """
    known = [s for s in states if s is not None]
    if not known or any(s != known[0] for s in known):
        return None
    return known[0]


def _mention_rule_line(full: bool | None) -> str:
    """「@其他成员」那条规矩：按群的实际模式给；不知道就两句都讲（这条不能猜）"""
    if full is True:
        return (
            "- 这个群开着官方**全量消息**：群里每个人说的话都会进 Copree、你都能看到；"
            "对方 @ 别人时会显示成 `<@!id>`，正文**不会**在 @ 处断掉。"
        )
    if full is False:
        return (
            "- 官方接口**不转发「@其他成员」的内容**：对方一条消息里同时 @ 了别人和你时，你收到的正文会在那个 @ 处"
            "断掉——这是通道限制，不是对方没说完整；需要就问一句「你刚才 @ 的是谁」。"
        )
    return (
        "- 「@其他成员」的内容：群里开着官方全量消息时正文是完整的、@ 会显示成 `<@!id>`；"
        "没开时正文会在那个 @ 处断掉——分不清就问一句「你刚才 @ 的是谁」。"
    )


async def _plugin_enabled(db: AsyncSession, plugin_id: str) -> bool:
    """通道要管理员在控制台把插件全局打开；关了就是"这个功能没开"，不是用户能自己绕过的开关"""
    from app.models.plugin import Plugin

    row = await db.get(Plugin, plugin_id)
    return bool(row and row.enabled)


async def views(db: AsyncSession, agent_id: int, user_id: int) -> list[dict[str, Any]]:
    """这个 AI 的全部通道视图（配置、运行态、配对名单）——有几条通道由插件说了算"""
    options = await group_options(db, agent_id, user_id)
    return [
        await _view_one(db, declared_channel=ch, agent_id=agent_id, user_id=user_id, group_options=options)
        for ch in all_declared()
    ]


async def _view_one(
    db: AsyncSession, *, declared_channel: dict[str, Any], agent_id: int, user_id: int,
    group_options: list[dict[str, Any]],
) -> dict[str, Any]:
    from app.services.infrastructure.plugin_registry import PluginRegistry, registry_key

    from app.services.plugin.skill_bridge import ensure_declared

    plugin_id = declared_channel["plugin_id"]
    # 卡片要能画出表单，先确保插件的声明已加载（拿不到 schema 就画不出字段）
    ensure_declared(plugin_id)
    instance = instance_of(agent_id)
    masked = await plugin_config.mask_config(plugin_id, instance, db=db)
    schema = await plugin_config.get_schema(plugin_id)
    rows = await pairing.list_rows(db, kind=declared_channel["kind"], owner_scope=instance)
    plugin = PluginRegistry.get(registry_key(plugin_id, instance))
    running = False
    detail: dict = {}
    if plugin is None:
        # 还没有实例（用户尚未配置）：托管协议端的登录状态仍要能看到，否则卡片上
        # 既没有扫码入口、也没有别的地方能扫码
        from app.services.plugin import api as plugin_api

        definition = plugin_api.get_service_def(plugin_id)
        hosted = await definition.cls.hosted_status() if definition else None
        if hosted:
            detail = {"hosted_endpoint": hosted}
    else:
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
    # 已批准的那位：外部通道一侧一人一实例，卡片上只展示一个"当前放行的人"
    owner_row = next((r for r in rows if r.status == pairing.APPROVED), None)
    return {
        "plugin_id": plugin_id,
        "kind": declared_channel["kind"],
        # 通道名是插件的产品名，三语都由 manifest 带（见 catalog.channels()）
        "label": declared_channel["label"],
        "label_en": declared_channel["label_en"],
        "label_ja": declared_channel["label_ja"],
        "desc": declared_channel["desc"],
        "desc_en": declared_channel["desc_en"],
        "desc_ja": declared_channel["desc_ja"],
        "guide": declared_channel["guide"],
        "limits": declared_channel["limits"],
        "pairing": declared_channel["pairing"],
        "supports_group": declared_channel["supports_group"],
        "instance": instance,
        "enabled": await _plugin_enabled(db, plugin_id),
        "schema": schema or {},
        "values": masked["values"],
        "secrets": masked["secrets"],
        "running": running,
        # 能不能自测由插件说了算：画不画那个按钮不猜，问插件
        "self_test": bool(plugin is not None and plugin.self_testable),
        "detail": detail,
        "missing_required": missing,
        "configured": bool(masked["values"]) or any(masked["secrets"].values()),
        "owner": owner_row and {
            "origin": owner_row.origin,
            "nickname": owner_row.display_name,
            "approved_at": owner_row.approved_at,
        } or None,
        "pending": [
            {"id": r.id, "origin": r.origin, "nickname": r.display_name, "code": r.code, "created_at": r.created_at}
            for r in rows if r.status == pairing.PENDING
        ],
        "approved": [
            {"id": r.id, "origin": r.origin, "nickname": r.display_name, "approved_at": r.approved_at}
            for r in rows if r.status == pairing.APPROVED
        ],
        "blocked": [
            {"id": r.id, "origin": r.origin, "nickname": r.display_name}
            for r in rows if r.status == pairing.BLOCKED
        ],
        # 前端下拉用：能接的 Copree 群（你管的 + 这个 AI 已经在里面的）
        "group_options": group_options,
    }


async def save(
    db: AsyncSession, *, plugin_id: str, agent_id: int, user_id: int, values: dict,
    actor: str, target_agent_name: str,
) -> dict[str, Any]:
    """保存通道配置并让它生效：写配置 → 建实例 → 启动。

    target_agent 不由用户填：入口在某个 AI 的页面上，它就是那个 AI ——
    让用户自己填名字，等于允许把机器人指到别人的 AI 上。
    """
    from app.services.infrastructure.plugin_registry import registry_key
    from app.services.plugin.skill_bridge import apply_skill_plugins, ensure_declared

    ch = declared(plugin_id)
    if not await _plugin_enabled(db, plugin_id):
        raise PermissionError(f"管理员还没有开放{ch['label']}通道")
    ensure_declared(plugin_id)
    # 允许部分保存（只交白名单、只改策略都算）：缺什么由 missing_required 提示、
    # 启动时插件自己会报"未配置: xxx"。以前这里要求"每次都得带凭据"，
    # 配合 set_config 的"空串=清除"语义，会把已存的机密抹掉。

    payload = dict(values)
    payload["target_agent"] = target_agent_name

    # 接了群就必须是"你管的、且这个 AI 已经在里面的"群：否则外部消息会落进别人的群
    raw_group = str(payload.get("copree_group_id") or "").strip()
    if raw_group:
        allowed = {int(g["id"]) for g in await group_options(db, agent_id, user_id)}
        if not raw_group.isdigit() or int(raw_group) not in allowed:
            raise ValueError("这个群不能接：只能选你管理、并且这个 AI 已经在里面的 Copree 群")

    instance = instance_of(agent_id)
    await plugin_config.set_config(plugin_id, payload, instance=instance, actor=actor, db=db)
    # 实例要先在注册表里存在才能启停：reconcile 是幂等的，多跑一次没关系
    await apply_skill_plugins(db)

    warning = ""
    running = False
    try:
        # 配置改了要重新读：先停再起（运行中直接 start 只会回一句"已在运行"，改动不生效）
        await runtime_control.stop_instance(db, registry_key(plugin_id, instance))
    except UnknownInstance:
        pass
    try:
        result = await runtime_control.start_instance(db, registry_key(plugin_id, instance))
        running, warning = bool(result.get("running")), ""
    except UnknownInstance:
        warning = f"配置已保存，但实例还没起来（请确认管理员已开放{ch['label']}通道）"
    except StartFailed as e:
        warning = "配置已保存，但" + str(e)
    return {"running": running, "warning": warning}


async def start(db: AsyncSession, *, plugin_id: str, agent_id: int) -> dict[str, Any]:
    from app.services.infrastructure.plugin_registry import registry_key

    ch = declared(plugin_id)
    if not await _plugin_enabled(db, plugin_id):
        raise PermissionError(f"管理员还没有开放{ch['label']}通道")
    return await runtime_control.start_instance(db, registry_key(plugin_id, instance_of(agent_id)))


async def stop(db: AsyncSession, *, plugin_id: str, agent_id: int) -> dict[str, Any]:
    from app.services.infrastructure.plugin_registry import registry_key

    declared(plugin_id)
    return await runtime_control.stop_instance(db, registry_key(plugin_id, instance_of(agent_id)))


async def self_test(*, plugin_id: str, agent_id: int) -> dict[str, Any]:
    """通道自测：让插件在自己的真实出口上发一条，把通道侧的原始响应带回来。

    刻意不走 runtime_control：自测要答的是"现在这条链路通不通"，
    没起来就该说没起来，而不是顺手把它拉起来把问题盖过去。
    """
    from app.services.infrastructure.plugin_registry import PluginRegistry, registry_key

    declared(plugin_id)
    key = registry_key(plugin_id, instance_of(agent_id))
    plugin = PluginRegistry.get(key)
    if plugin is None:
        raise UnknownInstance(key)
    if not plugin.self_testable:
        raise NoSelfTest(f"{plugin.display_name()} 没有可自测的出口")
    result = await plugin.self_test()
    if result is None:
        raise NoSelfTest(f"{plugin.display_name()} 没有可自测的出口")
    return result


async def approve(db: AsyncSession, *, plugin_id: str, agent_id: int, pairing_id: int | None = None, code: str | None = None) -> dict:
    ch = declared(plugin_id)
    row = await pairing.approve(
        db, kind=ch["kind"], owner_scope=instance_of(agent_id), pairing_id=pairing_id, code=code
    )
    return {"origin": row.origin, "nickname": row.display_name, "status": row.status}


async def block_pairing(db: AsyncSession, *, plugin_id: str, agent_id: int, origin: str) -> bool:
    """拉黑一个人（界面上的"不再回话，也不再发配对码"）"""
    ch = declared(plugin_id)
    row = await pairing.set_status(
        db, kind=ch["kind"], owner_scope=instance_of(agent_id), origin=origin, status=pairing.BLOCKED,
    )
    return row is not None


async def forget_pairing(db: AsyncSession, *, plugin_id: str, agent_id: int, origin: str) -> bool:
    """解除配对：对方重新变回陌生人，下次私聊重新领码"""
    ch = declared(plugin_id)
    return await pairing.forget(db, kind=ch["kind"], owner_scope=instance_of(agent_id), origin=origin)
