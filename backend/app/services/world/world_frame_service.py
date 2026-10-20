"""世界帧 —— 「进入一个世界」= 激活一条该世界专属的状态帧（沉浸式上下文）。

一个世界一条帧（type=world、context_ref=world:{id}、group_id=通道群），同一个世界再进是
**复活原帧**，不叠新的——「绑定时预建」和「进门时激活」必须指向同一条帧，否则一进就多一条。
帧规则见 docs/dev/frame_lifecycle.md，能力边界见 docs/group_world/design/world_agent_capabilities.md。

世界帧的工具集是它最重要的语义，也是本模块存在的理由：
- 基础集（切帧 / 退出 / 换世界 / 世界文本命令）+ **该世界当前颁布的世界侧 skills**；
- 其余平台工具在帧激活期间不出现——在世界里，AI 就是这个世界接进来的那个 AI，
  不会把群聊社交那套（发消息、翻记录、查群）带进游戏。

白名单**现算**（world_frame_tools / world_frame_tools_for），不冻结在帧上：世界后来颁布了新
技能，下一轮就能用。冻结一份旧清单会出现「能力清单里有它、工具列表里调不动」，比不给更难解释。
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.utils.pure.state_stack import ENDED_STATUS, make_state_frame, world_id_of

logger = logging.getLogger(__name__)

# 世界帧里保留的基础工具：它们不属于任何世界，但离开世界、换世界、看自己在哪，
# 以及走文本命令（路径 B：世界程序只有命令语法、没颁布 skills 时全靠它）都要用。
# 刻意短——「沉浸」有一半是靠这里**什么都不多给**；加一个工具前先问：它是不是世界的一部分。
WORLD_FRAME_BASE_TOOLS: tuple[str, ...] = (
    "enter_world",
    "world_command",
    "pop_state",
    "push_state",
    "list_states",
    "update_self_config",
)


async def _type_slugs(db: AsyncSession, agent, world_id: int,
                     channel_group_id: int | None = None) -> list[str]:
    """这个 AI 在这个世界里落到的绑定类型（agent 绑定 + 通道群绑定）。

    与群聊注入世界技能同一口径（world_agent_capabilities.md 三·六 的分层注入）：
    类型决定"哪些技能给他"，白名单不能比实际注入的更宽，否则会出现"清单里有、调不动"。
    """
    from app.models.world import WorldBinding

    entity_ids = [int(agent.user_id or 0)] + ([int(channel_group_id)] if channel_group_id else [])
    rows = (await db.execute(select(WorldBinding).where(
        WorldBinding.world_id == int(world_id),
        WorldBinding.entity_type.in_(("agent", "group")),
        WorldBinding.entity_id.in_(entity_ids),
    ))).scalars().all()
    return sorted({b.group_type_slug for b in rows if b.group_type_slug})


def _skill_tools(world_id: int, type_slugs: list[str]) -> list[dict]:
    """这个世界按绑定类型发给他的技能工具定义（多类型取并集，同名只留一个）。"""
    from app.services.world.world_skill_runtime import build_world_tools_for_type

    defs: list[dict] = []
    seen: set[str] = set()
    for slug in (type_slugs or [None]):
        for tool in build_world_tools_for_type(int(world_id), slug):
            name = str(((tool or {}).get("function") or {}).get("name") or "")
            if name and name not in seen:
                seen.add(name)
                defs.append(tool)
    return defs


def world_frame_tools(world_id: int) -> list[str]:
    """世界帧工具名（不区分 AI 类型）：基础集 + 这个世界**通用**的技能。"""
    names = {((t.get("function") or {}).get("name") or "") for t in _skill_tools(world_id, [])}
    return sorted({*WORLD_FRAME_BASE_TOOLS, *(n for n in names if n)})


async def world_frame_tools_for(db: AsyncSession, agent, world_id: int,
                                channel_group_id: int | None = None) -> list[str]:
    """按这个 AI 的绑定类型算白名单：基础集 + 它能用的世界侧技能。"""
    types = await _type_slugs(db, agent, world_id, channel_group_id)
    names = {((t.get("function") or {}).get("name") or "") for t in _skill_tools(world_id, types)}
    return sorted({*WORLD_FRAME_BASE_TOOLS, *(n for n in names if n)})


async def world_tools_for_agent(db: AsyncSession, agent, world_id: int,
                                channel_group_id: int | None = None) -> list[dict]:
    """这个世界对这个 AI 可用的技能工具定义（私信路径注入用；群聊路径已有同一套过滤）。"""
    return _skill_tools(world_id, await _type_slugs(db, agent, world_id, channel_group_id))


async def frame_tool_gate(db: AsyncSession, agent_id: int) -> set[str] | None:
    """当前帧允许的工具名（None = 不隔离）。群聊与私信两条路径共用这一份判定。

    普通会话帧读帧上存的 tools/skills；世界帧现算（见 world_frame_tools）。
    """
    from app.services.agent.state_stack_service import get_active_frame_tools

    tools, skills = await get_active_frame_tools(db, agent_id)
    if tools is not None:
        return set(tools)
    if skills is not None:
        return set(skills)
    return None


async def active_world(db: AsyncSession, agent_id: int) -> tuple[int | None, int | None]:
    """他现在正在哪个世界里 → (世界号, 通道群)；不在世界里返回 (None, None)。

    通道群要一起给：按类型过滤技能时要知道"从哪个群进来的"（群类型决定他落到的类型）。
    """
    from app.services.agent.state_stack_service import get_current_frame

    frame = await get_current_frame(db, agent_id)
    world_id = world_id_of(frame)
    if world_id is None:
        return None, None
    raw_group = frame.get("group_id")
    try:
        return world_id, (int(raw_group) if raw_group else None)
    except (TypeError, ValueError):
        return world_id, None


def _dormant_world_frame(world) -> dict:
    """一条**沉睡**世界帧：先建好放着，进世界时才激活（不改变他现在在哪）。"""
    frame = make_state_frame(
        type_="world",
        context_ref=f"world:{world.id}",
        label=f"世界「{world.name}」",
        why="绑定世界时预建",
        doing=f"待进入世界「{world.name}」",
    )
    frame["status"] = ENDED_STATUS
    return frame


async def _frames_of_world(db: AsyncSession, agent_id: int, world_id: int) -> list[dict]:
    """这个世界的帧（**含已结束的沉睡帧**）——按 context_ref 认，不按状态。

    只认运行集合会把绑定时预建的那条当成"没有"，一进世界又建一条（同一 ref 两条帧）。
    """
    from app.services.agent.state_stack_service import get_frames

    ref = f"world:{world_id}"
    return [f for f in await get_frames(db, agent_id) if f.get("context_ref") == ref]


async def park_world_frame(db: AsyncSession, agent_id: int, world) -> bool:
    """绑定世界时预建一条沉睡世界帧：已经有了就什么都不做。返回是否新建。"""
    from app.services.agent.state_stack_service import park_frame

    if await _frames_of_world(db, agent_id, world.id):
        return False
    await park_frame(db, agent_id, _dormant_world_frame(world))
    logger.info(f"🌍 Agent({agent_id}) 预建世界帧：世界 #{world.id}「{world.name}」")
    return True


async def park_world_frame_for_binding(db: AsyncSession, entity_id: int, world) -> bool:
    """入口绑定 entity_type='agent' 用：entity_id 是 user_id，换成 AI 再预建。

    绑定必须成功、预建只是顺带：这里吞异常只记日志——预建失败最多是"进门时才建帧"，
    不该让世界绑不上（绑定是用户的操作，预建是平台的记账）。
    """
    from app.models.agent import Agent

    try:
        agent = (await db.execute(
            select(Agent).where(Agent.user_id == int(entity_id))
        )).scalar_one_or_none()
        if agent is None:
            return False
        return await park_world_frame(db, agent.id, world)
    except Exception as e:  # pragma: no cover - 防御：预建不是绑定的前置条件
        logger.warning(
            f"🌍 预建世界帧失败（绑定照旧）：world={getattr(world, 'id', '?')} entity={entity_id}: {e}")
        return False


async def activate_world_frame(db: AsyncSession, agent_id: int, world, target_group: int,
                               group_name: str, why: str = "") -> tuple[dict, bool]:
    """进世界：把这条世界帧设为当前帧（已有就复活，没有才建）。返回 (帧, 是否复活)。

    通道群（group_id）每次进门都刷新：一个世界可能绑好几个群，命令要落到这次进来的那个群，
    但帧身份（id、context_ref）不变。
    """
    from app.services.agent.state_stack_service import push_state, restore_frame

    doing = f"在世界「{world.name}」里游玩（通道：群「{group_name}」）"
    label = f"世界「{world.name}」"
    existing = await _frames_of_world(db, agent_id, world.id)
    if existing:
        # 归一化后的存储里历史区在前、运行区在后，取末位就是"这条世界帧"（有活在跑的优先）
        # handoff=True：进世界是从别处切过来，要带上"刚在哪个会话说话"的交接包
        frame = await restore_frame(db, agent_id, str(existing[-1].get("id") or ""), updates={
            "group_id": target_group,
            "label": label,
            "doing": doing,
        }, handoff=True)
        return frame, True

    frame = make_state_frame(
        type_="world",
        context_ref=f"world:{world.id}",
        group_id=target_group,
        label=label,
        why=why or f"{group_name} 的世界",
        doing=doing,
    )
    await push_state(db, agent_id, frame)
    return frame, False
