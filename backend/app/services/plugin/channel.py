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

import json
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


async def group_options(
    db: AsyncSession, agent_id: int, user_id: int, *,
    include_unjoined: bool = False, query: str = "",
) -> list[dict[str, Any]]:
    """可以把这个 AI 接进去的 Copree 群：**你管的**（群主/管理员）。

    默认再要求**它已经在里面**：只列你管理的群，别人不会因为你的机器人被塞进他们的群；
    只列它已经是成员的群，否则 QQ 消息落进去、它却不在群里，等于往别人群里灌消息。
    include_unjoined=True 给"合并/指落点"用：你管的群都能选，定下落点时平台顺手把这个 AI
    加进去（见 attach_landing）——比"先手动把它拉进群，才出现在候选里"少一步。
    query 按群名搜（群多了要翻得动）。
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
        .where(Group.archived_at.is_(None))
        .where(or_(Group.id.in_(groups_i_manage), Group.id.in_(groups_i_own)))
        .order_by(Group.id.desc())
    )
    if not include_unjoined:
        stmt = stmt.where(Group.id.in_(groups_with_this_ai))
    if str(query or "").strip():
        stmt = stmt.where(Group.name.ilike(f"%{str(query).strip()}%"))
    return [{"id": int(r[0]), "name": r[1]} for r in (await db.execute(stmt)).all()]


async def create_landing_group(
    db: AsyncSession, *, agent_id: int, user_id: int, name: str, origin_channel: str | None = None,
) -> dict:
    """在 Copree 单独建一个群当外部消息的落点：你是群主，这个 AI 是成员。

    为什么走 create_group：建群还要带群主成员行、并发上限等一串约定，
    那些都住在 app/chat/gm.py 一处，不在这里再写一份。
    """
    from app.chat.gm import create_group
    from app.models.agent import Agent

    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise ValueError("AI 不存在")
    typed = (name or "").strip()
    clean = typed[:60] or (str(agent.name) + " 的群")
    group = await create_group(
        db, clean, "human", user_id, initial_members=[{"type": "ai", "id": agent.user_id}]
    )
    # 没起名＝接受兜底名：这种群的名字交给通道维护，通道侧群名有了就对齐（群设置里可关）
    group.name_from_channel = not typed
    # 记下"这条通道建的"：合并时只有这类群会被收走（用户自己建的群一律不动）
    group.origin_channel = (origin_channel or None)
    await db.commit()
    logger.info("为 AI #%s 建了落点群 #%s（%s）", agent_id, group.id, clean)
    return {"id": int(group.id), "name": group.name}


async def ensure_ai_in_group(db: AsyncSession, *, group_id: int, agent_id: int) -> bool:
    """保证这个 AI 是那个 Copree 群的成员：落点群就是它的舞台，指到哪儿就把它加进去。

    已经是成员就什么都不做（返回 False）。复用 gm.add_member（AI 成员统一用 user_id 当 member_id）。
    """
    from sqlalchemy import select

    from app.chat.gm import add_member
    from app.models.agent import Agent
    from app.models.group import GroupMember

    agent = await db.get(Agent, agent_id)
    if agent is None or not agent.user_id:
        return False
    member_id = int(agent.user_id)
    exists = (await db.execute(select(GroupMember).where(
        GroupMember.group_id == int(group_id),
        GroupMember.member_type == "ai",
        GroupMember.member_id == member_id,
    ))).scalar_one_or_none()
    if exists is not None:
        return False
    await add_member(db, int(group_id), "ai", member_id)
    logger.info("AI #%s 已加入落点群 #%s", agent_id, group_id)
    return True


async def _claim_landing(db: AsyncSession, *, agent_id: int, name: str) -> int:
    """认领已有落点：同一个主人的别的机器人已经把**同名**的通道群接到了某个 Copree 群 → 就是它。

    为什么只能按群名对号：同一个 QQ 群，每台机器人看到的群体标识是**各自 app 的 openid**，
    官方也不给群号（成员侧同理——见 models/external.py 那句"同一个人在每个机器人眼里各是一个
    openid"），所以跨机器人唯一对得上的就是群名（外加人数这类同样只能当线索的东西）。
    限定"同一个主人的机器人之间"：认错了也只会落在自己的群里，不会把 AI 塞进别人的群。
    """
    wanted = str(name or "").strip()
    if not wanted:
        return 0
    from app.models.agent import Agent

    mine_agent = await db.get(Agent, agent_id)
    if mine_agent is None:
        return 0
    mine = instance_of(agent_id)
    for (plugin_id, instance), slot in (await _channel_landings(db)).items():
        if instance == mine:
            continue
        other_id = agent_id_of(str(instance))
        if other_id is None:
            continue
        other = await db.get(Agent, other_id)
        if other is None or int(other.owner_id) != int(mine_agent.owner_id):
            continue
        candidates = {int(slot["default"] or 0)} | {int(v or 0) for v in slot["map"].values()}
        for group_id in sorted(g for g in candidates if g):
            got = await channel_group_name(db, group_id)
            if got and got.strip() == wanted:
                logger.info(
                    "通道群「%s」认到已有落点群 #%s（来自实例 %s/%s）", wanted, group_id, plugin_id, instance
                )
                return group_id
    return 0


async def _read_landings(db: AsyncSession, *, plugin_id: str, agent_id: int) -> tuple[dict[str, int], int]:
    """这个实例当前生效的落点（group_map + 默认落点群）：一律现读库，别信内存副本"""
    from app.utils.pure.channel_landing import parse_group_map

    current = await plugin_config.get_config(plugin_id, instance_of(agent_id), db=db)
    mapping = parse_group_map(current.get("group_map"))[0]
    try:
        default_id = int(str(current.get("copree_group_id") or 0) or 0)
    except ValueError:
        default_id = 0
    return mapping, default_id


async def _write_landing(db: AsyncSession, *, plugin_id: str, agent_id: int, origin: str, group_id: int) -> None:
    """把这个通道群的落点写进该实例的 group_map（部分保存，不碰别的键）"""
    mapping, _default = await _read_landings(db, plugin_id=plugin_id, agent_id=agent_id)
    mapping[str(origin)] = int(group_id)
    await plugin_config.set_config(
        plugin_id, {"group_map": json.dumps(mapping, ensure_ascii=False)}, instance_of(agent_id),
        actor="auto", db=db,
    )


async def _retire_source_group(
    db: AsyncSession, *, plugin_id: str, agent_id: int, kind: str, previous: int, target: int,
    mapping: dict[str, int], default_id: int,
) -> int:
    """合并之后收走源落点群：只收"本通道自己建的"，而且收之前确认再没有别的通道群指着它。

    为什么要有这两道闸：用户自己建的群不能因为一次落点调整就消失；通道建的群只要还有别的
    QQ 群（或本实例的默认落点）指着，它就还在用。收走＝归档（archived_at），数据一条不删。
    """
    if not previous or int(previous) == int(target):
        return 0
    from app.models.group import Group
    from app.utils.pure.timeutil import utc_now

    group = await db.get(Group, int(previous))
    if group is None or str(getattr(group, "origin_channel", "") or "") != str(kind):
        return 0
    landings = await _channel_landings(db)
    landings[(plugin_id, instance_of(agent_id))] = {"default": int(default_id or 0), "map": dict(mapping)}
    for slot in landings.values():
        if int(slot.get("default") or 0) == int(previous):
            return 0
        if any(int(v or 0) == int(previous) for v in (slot.get("map") or {}).values()):
            return 0
    group.archived_at = utc_now()
    logger.info("落点群 #%s 已并入 #%s，归档（不再有任何通道群指着它）", previous, target)
    return int(previous)


async def attach_landing(
    db: AsyncSession, *, plugin_id: str, agent_id: int, origin: str, group_id: int,
) -> int:
    """把一个通道群的落点指到某个 Copree 群 —— 手工选择、合并、自动认领都走这一条。

    只做三件事：这个 AI 进那个群 → 落点写进该实例的 group_map → 原来的落点群该收走就收走
    （见 _retire_source_group）。所以"合并"不是一个新机制，而是"把落点指过去"；
    "自动接住"也只是先自己找了个目标再走这里。
    """
    kind = str(declared(plugin_id)["kind"])
    mapping, default_id = await _read_landings(db, plugin_id=plugin_id, agent_id=agent_id)
    previous = int(mapping.get(str(origin)) or default_id or 0)
    await ensure_ai_in_group(db, group_id=int(group_id), agent_id=agent_id)
    mapping[str(origin)] = int(group_id)
    await plugin_config.set_config(
        plugin_id, {"group_map": json.dumps(mapping, ensure_ascii=False)}, instance_of(agent_id),
        actor="auto", db=db,
    )
    await _retire_source_group(
        db, plugin_id=plugin_id, agent_id=agent_id, kind=kind, previous=previous,
        target=int(group_id), mapping=mapping, default_id=default_id,
    )
    return int(group_id)


async def landing_suggestion(
    db: AsyncSession, *, agent_id: int, name: str, exclude_group_id: int = 0,
) -> dict[str, Any] | None:
    """这个通道群"可能是同一个群"的那个 Copree 群：卡片显示 + 一键合并用（只读，不落任何东西）。

    已经指着它（exclude）就没什么可合并的，返回 None——"有没有落点"不影响这条提示：
    用户常常是先建了个落点群，才发现另一个机器人早把同一个 QQ 群接在别处了。
    """
    from app.models.group import Group

    group_id = await _claim_landing(db, agent_id=agent_id, name=str(name or ""))
    if not group_id or int(group_id) == int(exclude_group_id or 0):
        return None
    row = await db.get(Group, int(group_id))
    return {"id": int(group_id), "name": str(getattr(row, "name", "") or f"群{group_id}")}


async def claim_landing(db: AsyncSession, *, plugin_id: str, agent_id: int, origin: str, name: str) -> int:
    """认领同名已有群（认到就等于接上，不建任何东西）；认不到返回 0。

    "被拉进群"那一刻只做这一步：有对应的群立刻接上，没对应的等第一条消息再建——
    群可能只是被拉进去、没人说话，那一刻建出来的是空群。
    """
    group_id = await _claim_landing(db, agent_id=agent_id, name=name)
    if not group_id:
        return 0
    return await attach_landing(db, plugin_id=plugin_id, agent_id=agent_id, origin=origin, group_id=group_id)


async def resolve_landing(
    db: AsyncSession, *, plugin_id: str, agent_id: int, origin: str, name: str = "", create: bool = True,
) -> int:
    """通道侧的群该落到哪个 Copree 群 —— 自动接住的唯一入口：认领同名已有群 → 新建一个。

    两条路都保证「这个 AI 是那个群的成员」，也都写进 group_map：与手工指定的落点是同一份配置、
    同一条查表路径（不是两套机制）。建群走 create_landing_group（群主成员行那串约定在 gm.py 一处）。
    create=False = 只认领，不新建（见 claim_landing）。
    """
    target = str(origin or "").strip()
    if not target:
        raise ValueError("通道侧群标识为空，不排落点")
    from app.models.agent import Agent

    claimed = await claim_landing(db, plugin_id=plugin_id, agent_id=agent_id, origin=target, name=name)
    if claimed or not create:
        return claimed
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise ValueError(f"AI 不存在（{agent_id}）")
    # 起名留空＝接受兜底名（<AI 名> 的群），名字交给通道侧对齐：QQ 群改名它跟着改
    created = await create_landing_group(
        db, agent_id=agent_id, user_id=int(agent.owner_id), name="",
        origin_channel=str(declared(plugin_id)["kind"]),
    )
    group_id = int(created["id"])
    logger.info("通道群 %s 首次说话 → 新建落点群 #%s（AI #%s）", target[-8:], group_id, agent_id)
    return await attach_landing(db, plugin_id=plugin_id, agent_id=agent_id, origin=target, group_id=group_id)


async def _channel_landings(db: AsyncSession) -> dict[tuple[str, str], dict[str, Any]]:
    """每个"属于某个 AI 的通道实例"的落点：默认落点群 + 群映射表（group_map）

    两个键一起取：一个群可能是实例的默认落点，也可能是某个通道群单独指定的落点。
    只看默认落点会漏掉后者——那个群里的 AI 就不知道通道的规矩了。
    """
    from sqlalchemy import select

    from app.models.plugin import PluginConfig
    from app.utils.pure.channel_landing import parse_group_map

    rows = (await db.execute(
        select(PluginConfig.plugin_id, PluginConfig.instance, PluginConfig.key, PluginConfig.value).where(
            PluginConfig.key.in_(("copree_group_id", "group_map"))
        )
    )).all()
    landings: dict[tuple[str, str], dict[str, Any]] = {}
    for plugin_id, instance, key, value in rows:
        if agent_id_of(str(instance)) is None:
            continue                      # 不是"某个 AI 的通道"就不是任何群的出口
        slot = landings.setdefault((str(plugin_id), str(instance)), {"default": 0, "map": {}})
        if key == "group_map":
            slot["map"] = parse_group_map(value)[0]
        else:
            try:
                slot["default"] = int(str(value or 0))
            except ValueError:
                slot["default"] = 0
    return landings


async def served_instances(db: AsyncSession, group_id: int) -> list[tuple[str, str, dict[str, Any]]]:
    """哪些通道实例接着这个群 → [(plugin_id, instance, 通道声明)]

    落点规则与插件落消息时是同一份（app.utils.pure.channel_landing）：两边各写一遍，
    迟早会出现"消息落在这个群、平台却说这个群没接通道"。
    """
    from app.utils.pure.channel_landing import serves_group

    out: list[tuple[str, str, dict[str, Any]]] = []
    for (plugin_id, instance), slot in (await _channel_landings(db)).items():
        if not serves_group(group_map=slot["map"], default_group_id=slot["default"], group_id=group_id):
            continue
        found = catalog.channel_plugin(plugin_id)
        if found:
            out.append((plugin_id, instance, found))
    return out


async def bound_group_labels(db: AsyncSession) -> dict[int, list[str]]:
    """被外部通道接着的 Copree 群 → 那些通道的名字（如 ['QQ']）

    只算还声明着的通道（插件可能已被卸载）：按钮点了没反应的开关比没有开关更糟。
    """
    out: dict[int, list[str]] = {}
    for (plugin_id, _instance), slot in (await _channel_landings(db)).items():
        found = catalog.channel_plugin(plugin_id)
        if not found:
            continue
        group_ids: set[int] = set()
        if slot["default"]:
            group_ids.add(int(slot["default"]))
        group_ids.update(int(v) for v in slot["map"].values() if int(v or 0))
        for group_id in group_ids:
            labels = out.setdefault(group_id, [])
            if found["label"] not in labels:
                labels.append(found["label"])
    return out


async def channel_group_name(db: AsyncSession, group_id: int) -> str:
    """这个群在通道那边的真名：打开"跟随通道群名"要立刻见效，所以手上有就用、没有就问一次

    拿不到就返回空串——通道侧没有群名这个概念（或还不知道是哪个通道群）时，群名保持原样，
    等下一条通道消息来了自然会补上。
    """
    for plugin_id, instance, _found in await served_instances(db, group_id):
        facts = _live_group_facts(plugin_id, instance, group_id)
        if not facts:
            facts = await _refresh_live_group_facts(plugin_id, instance, group_id, force=True)
        name = str((facts or {}).get("name") or "").strip()
        if name:
            return name
    return ""


async def _refresh_live_group_facts(
    plugin_id: str, instance: str, group_id: int, *, force: bool
) -> dict[str, Any] | None:
    """让活着的实例现拉一次通道侧群信息（用户正看着界面）；失败当作"还没有"

    触发点都是用户动作（按开关、打开资料卡），所以机会不多；force=False 仍受插件那边的
    "今天问过就用手里的"约束——资料卡会被反复打开，不能每次都去问通道。
    """
    from app.services.infrastructure.plugin_registry import get_by_owner

    plugin = get_by_owner(plugin_id, instance)
    probe = getattr(plugin, "refresh_group_facts", None)
    if not callable(probe):
        return None
    try:
        return await probe(group_id, force=force)
    except Exception as e:
        logger.warning(f"现拉通道群信息失败（非致命）: {type(e).__name__}: {e}")
        return None


async def describe_group(db: AsyncSession, group_id: int, *, refresh: bool = False) -> list[dict[str, Any]]:
    """这个群在通道那边是什么样（群名/人数/简介/分类/标签）：资料卡显示"它在 QQ 里叫什么"

    手上有就用，没有就问一次（同一天只问一次）——资料卡是用户主动打开的，等不到下一条消息。
    refresh=True 给"手动同步"按钮：现在就问，不再看今天问过没有。
    拿不到就返回空列表，资料卡不显示通道那一段。
    """
    out: list[dict[str, Any]] = []
    for plugin_id, instance, found in await served_instances(db, group_id):
        facts = _live_group_facts(plugin_id, instance, group_id)
        if not facts or refresh:
            facts = await _refresh_live_group_facts(plugin_id, instance, group_id, force=refresh)
        if not facts or not str(facts.get("name") or "").strip():
            continue
        out.append({**facts, "kind": found["kind"], "label": found["label"]})
    return out


async def files_supported(db: AsyncSession, group_id: int, *, served: list | None = None) -> bool:
    """这个群出站带不带得了附件 —— send_file 与「给 AI 讲通道规矩」共用的一份判定

    没有通道 = 站内群，附件随便发。有通道时看插件自己声明的 supports_files：任一还在接着
    这个群的通道报 false，就按"发出去对面也收不到"算——多通道并存时取最保守的那个，
    理由同 channel_rules：说错了 AI 会拿它当事实用。
    """
    found = served if served is not None else await served_instances(db, group_id)
    return all(bool(declared.get("supports_files", True)) for _, _, declared in found)


async def channel_rules(db: AsyncSession, group_id: int, *, served: list | None = None) -> str:
    """这个群经不经过外部通道、那条通道有什么规矩 —— 给 AI 的一段话（没有通道就返回空串）

    为什么由平台注入，而不是让 AI 自己猜：消息从 QQ 来这件事背后有一串接口约束
    （腾讯 2025-04-21 起下线了主动推送），不说清它就会答应"我待会儿在群里提醒你"，
    然后什么都发不出去。

    为什么这段话能进锁定前缀：它只由库里的通道绑定（served_instances）与插件声明的能力
    算出来，不掺任何运行期观测。它每轮进 message 0，抖一个字节整份上下文就得重算一遍。
    随群播报模式变的那条 @ 规矩是运行期观测，在 live_mention_rule 里走尾部读数。
    """
    found_channels: list[dict[str, Any]] = []
    for _plugin_id, _instance, found in (served if served is not None else await served_instances(db, group_id)):
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
            "- 官方 QQ 机器人：腾讯自 2025-04-21 起下线了主动推送；**对方一条消息你最多只能回 5 句**"
            "（群 5 分钟、私聊 60 分钟内有效，超时或超数都发不出去）——省着说，一句能说完就别拆三句。"
        )
        lines.append(
            "- 官方机器人私聊**可以**主动发消息，但每天每个用户最多 2 条：留给要紧的提醒，别用来说废话。"
        )
        lines.append("- 你写的 Markdown 会尽量按富文本发（机器人没开通 Markdown 权限时平台会自动降级成纯文本）。")
        lines.append("- **QQ 用户看不到 Copree 侧（此侧）的 ID**。")
        lines.append(
            "- **撤回不同步**：QQ 里别人撤回的消息我们收不到通知，你上下文里那条还在（就当它发生过）；"
            "反过来你在站内撤回（2 分钟内）我们会请 QQ 一起撤，超时就只有站内撤掉。"
        )
    if "qq-napcat" in kinds:
        lines.append(
            "- NapCat 通道用的是真 QQ 号（协议端）：没有上面那些接口窗口限制，但同样别刷屏，"
            "并且富文本能不能渲染取决于 QQ 客户端。"
        )
    if not await files_supported(db, group_id, served=served):
        lines.append(
            f"- **发不了文件**（{labels} 侧）：这条通道没有接收附件的出口，send_file 只会留在站内，"
            "那边的人收不到。要传内容就把正文写成文字，或给出一个能打开的链接。"
        )
    return "\n".join(lines)


async def live_mention_rule(db: AsyncSession, group_id: int, *, served: list | None = None) -> str:
    """「@其他成员」那条规矩 —— 走**尾部读数**（随群播报模式变，问活着的实例才知道）

    为什么不能锁进前缀：模式是插件观测事件类型得到的运行期事实（重启后要重新观测，
    还会从"不知道"变成"知道"），锁进去就等于让每个群的前缀随时可能重算。
    读数是每轮重拼的一行，几十字，比整份上下文重算便宜得多。

    问不到就两句话都讲，不猜——猜错了 AI 会当事实用。
    """
    modes: list[bool | None] = []
    for plugin_id, instance, found in (served if served is not None else await served_instances(db, group_id)):
        if found["kind"] == "qq":
            modes.append(_live_full_mode(plugin_id, instance))
    if not modes:
        return ""
    return _mention_rule_line(_brief_full_mode(modes))


def _live_group_facts(plugin_id: str, instance: str, group_id: int) -> dict[str, Any] | None:
    """问活着的插件实例：这个 Copree 群对应的通道侧群叫什么、多大（拿不到就 None）

    与推送模式同一口径：这是运行期观测（今天拉到的群信息），不落库；重启后第一条群消息重新拉。
    """
    from app.services.infrastructure.plugin_registry import get_by_owner

    plugin = get_by_owner(plugin_id, instance)
    probe = getattr(plugin, "facts_for_group", None)
    return probe(group_id) if callable(probe) else None



def _live_full_mode(plugin_id: str, instance: str) -> bool | None:
    """问活着的插件实例：这个群最近一次观测到的推送模式（拿不到就 None）

    为什么不落库：这是运行期观测（事件类型），不是配置；重启后第一条群消息就能重新观测到。
    给 AI 的**持久**记录在账本里（QQ 插件投的「通道变更」通知），不靠这里。
    """
    from app.services.infrastructure.plugin_registry import get_by_owner

    plugin = get_by_owner(plugin_id, instance)
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
    # 卡片里的落点候选 = 你管理的所有群（合并/指落点都要选得到；选中后平台会把这个 AI 加进去）
    options = await group_options(db, agent_id, user_id, include_unjoined=True)
    return [
        await _view_one(db, declared_channel=ch, agent_id=agent_id, user_id=user_id, group_options=options)
        for ch in all_declared()
    ]


async def _view_one(
    db: AsyncSession, *, declared_channel: dict[str, Any], agent_id: int, user_id: int,
    group_options: list[dict[str, Any]],
) -> dict[str, Any]:
    from app.services.infrastructure.plugin_registry import get_by_owner

    from app.services.plugin.skill_bridge import ensure_declared

    plugin_id = declared_channel["plugin_id"]
    # 卡片要能画出表单，先确保插件的声明已加载（拿不到 schema 就画不出字段）
    ensure_declared(plugin_id)
    instance = instance_of(agent_id)
    masked = await plugin_config.mask_config(plugin_id, instance, db=db)
    schema = await plugin_config.get_schema(plugin_id)
    rows = await pairing.list_rows(db, kind=declared_channel["kind"], owner_scope=instance)
    plugin = get_by_owner(plugin_id, instance)
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
        "supports_files": declared_channel["supports_files"],
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

    # 接了群就必须是"你管的"群：否则外部消息会落进别人的群。选中之后平台把这个 AI 加进去
    # （含"它还没进那个群"的情况——合并/换落点本来就是要把它搬过去）
    raw_group = str(payload.get("copree_group_id") or "").strip()
    if raw_group:
        allowed = {int(g["id"]) for g in await group_options(db, agent_id, user_id, include_unjoined=True)}
        if not raw_group.isdigit() or int(raw_group) not in allowed:
            raise ValueError("这个群不能接：只能选你管理（群主/管理员）的 Copree 群")
        await ensure_ai_in_group(db, group_id=int(raw_group), agent_id=agent_id)

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
    from app.services.infrastructure.plugin_registry import get_by_owner, registry_key

    declared(plugin_id)
    instance = instance_of(agent_id)
    plugin = get_by_owner(plugin_id, instance)
    if plugin is None:
        raise UnknownInstance(registry_key(plugin_id, instance))
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
