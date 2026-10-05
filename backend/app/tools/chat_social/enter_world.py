"""
enter_world 工具 — AI 进世界游玩（一个世界一个状态）

把 (世界, 通道群) 记成一条状态帧：type=world、context_ref=world:{id}、group_id=通道群。
同一个世界再进一次是**复活**原帧（只刷通道群与说法，why/todo/plan 是他自己的东西，留着），
不叠新的。

设计：docs/group_world/design/world_agent_capabilities.md
"""
from sqlalchemy.ext.asyncio import AsyncSession

from app.tools.base import ToolPlugin, ToolRegistry


class _EntryDenied(Exception):
    """进不去：把话原样回给 AI（工具的错误约定是 {"error": True, "message": ...}）。"""


async def _resolve_channel_group(db, agent_id: int, chat_group_id: int | None, arguments: dict) -> int:
    """命令从哪个群出去：显式参数 > 候选群里第一个绑世界的 > 最近的群（只为把错话说准）。"""
    from app.services.agent.state_stack_service import current_context
    from app.services.world.world_service import first_world_bound_group

    raw = arguments.get("group_id")
    if raw is not None:
        try:
            return int(raw)
        except (TypeError, ValueError):
            raise _EntryDenied("group_id 必须是群编号（整数）")

    ctx = await current_context(db, agent_id, chat_group_id)
    group, _ = await first_world_bound_group(db, ctx["group_ids"])
    group = group or (ctx["group_ids"][0] if ctx["group_ids"] else None)
    if group is None:
        raise _EntryDenied("不知道进哪个群的世界：你在私信里，请先 enter_group(群号) 或带上 group_id")
    return group


async def _resolve_world(db, agent, target_group: int, arguments: dict):
    """进哪个世界：显式 world_id（须绑该群或绑他）> 该群绑定的那个（多个必须点明）。"""
    from app.models.world import World
    from app.services.world.world_service import find_worlds_by_entity

    raw = arguments.get("world_id")
    if raw is None:
        worlds = await find_worlds_by_entity(db, "group", target_group)
        if not worlds:
            raise _EntryDenied(f"群 {target_group} 未绑定世界（可在世界列表给群配置世界后使用）")
        if len(worlds) > 1:
            names = "、".join(f"#{w.id}「{w.name}」" for w in worlds)
            raise _EntryDenied(f"群 {target_group} 绑了多个世界（{names}），请用 world_id 指定进哪个")
        return worlds[0]

    try:
        world_id = int(raw)
    except (TypeError, ValueError):
        raise _EntryDenied("world_id 必须是世界编号（整数）")
    if not any(w.id == world_id for w in await find_worlds_by_entity(db, "group", target_group)):
        # AI 直接绑定世界也算（世界技能那条路），但命令仍要从通道群出去
        mine = await find_worlds_by_entity(db, "agent", agent.user_id) if agent.user_id else []
        if not any(w.id == world_id for w in mine):
            raise _EntryDenied(f"世界 #{world_id} 没绑群 {target_group}，也没绑定你，进不去")
    world = await db.get(World, world_id)
    if world is None:
        raise _EntryDenied(f"世界 #{world_id} 不存在")   # 防御：绑定行还在、世界行刚被删
    return world


async def _ensure_member(db, group_id: int, agent_id: int) -> None:
    """命令要以 AI 身份发到那个群：不是成员就进不去（发送侧也会拒）。"""
    from app.chat.gm import _get_member
    if await _get_member(db, group_id, "ai", agent_id) is None:
        raise _EntryDenied(f"你不在群 {group_id} 里，进不去它的世界")


async def _activate_world_frame(db, agent_id: int, world, target_group: int,
                                group_name: str, why: str = "") -> tuple[dict, bool]:
    """一个世界一个状态：已有同世界的帧就复活它，没有才压新的。返回 (帧, 是否复活)。"""
    from app.services.agent.state_stack_service import get_frames, push_state, restore_frame
    from app.utils.pure.state_stack import context_frames, make_state_frame

    doing = f"在世界「{world.name}」里游玩（通道：群「{group_name}」）"
    existing = context_frames(await get_frames(db, agent_id), f"world:{world.id}")
    if existing:
        # 同一 ref 至多一帧（老数据的重复帧会在 _save 里合掉），所以 [0] 就是"那帧"
        frame = await restore_frame(db, agent_id, str(existing[0].get("id") or ""), updates={
            "group_id": target_group,
            "label": f"世界「{world.name}」",
            "doing": doing,
        })
        return frame, True
    frame = make_state_frame(
        type_="world",
        context_ref=f"world:{world.id}",
        group_id=target_group,
        label=f"世界「{world.name}」",
        why=why or f"{group_name} 的世界",
        doing=doing,
    )
    await push_state(db, agent_id, frame)
    return frame, False


class EnterWorld(ToolPlugin):
    name = "enter_world"
    description = (
        "进入一个世界游玩：状态切到这个世界（状态摘要里出现世界名），之后可以用 world_command "
        "操作它；出来用 pop_state。不带参数＝进入当前群绑定的世界，只有当前群绑了多个世界时"
        "才需要 world_id。"
    )
    segment = "chat_social"
    parameters = {
        "world_id": {
            "type": "integer",
            "nullable": True,
            "description": "世界编号（当前群绑定了多个世界时指定；不填＝当前群绑定的那个）",
        },
        "group_id": {
            "type": "integer",
            "nullable": True,
            "description": "世界的通道群：命令从这里出去（不填＝当前群，或你刚 enter_group 进去的群）",
        },
        "why": {"type": "string", "nullable": True, "description": "进世界做什么（可选）"},
    }
    required = []
    states = ["active", "dnd", "inactive"]
    admin_description = "AI 进入群绑定的世界游玩（一个世界一个状态，命令从通道群出去）"
    trigger_condition = "群绑定了世界，且 AI 要进这个世界玩时"

    async def execute(self, db: AsyncSession, agent_id: int, group_id: int | None,
                      arguments: dict, context: dict) -> dict:
        from app.models.agent import Agent
        from app.models.group import Group

        agent = context.get("_agent") or await db.get(Agent, agent_id)
        if agent is None:
            return {"error": True, "message": "AI 不存在，无法进世界"}

        try:
            target_group = await _resolve_channel_group(db, agent_id, group_id, arguments)
            world = await _resolve_world(db, agent, target_group, arguments)
            await _ensure_member(db, target_group, agent_id)
        except _EntryDenied as denied:
            return {"error": True, "message": str(denied)}

        group_row = await db.get(Group, target_group)
        group_name = group_row.name if group_row is not None else f"群{target_group}"
        _, restored = await _activate_world_frame(
            db, agent_id, world, target_group, group_name, str(arguments.get("why") or ""))
        # 不 commit：提交是调用者的事（work_session 出块即提交），与 world_command 一致

        return {
            "success": True,
            "world_id": world.id,
            "world_name": world.name,
            "group_id": target_group,
            "group_name": group_name,
            "restored": restored,
            "message": (f"已进入世界「{world.name}」（#{world.id}），命令从群「{group_name}」出去；"
                        "用 world_command 操作世界，出来用 pop_state"),
        }


ToolRegistry.register(EnterWorld)
