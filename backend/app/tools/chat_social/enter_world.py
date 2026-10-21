"""
enter_world 工具 — AI 进群视界游玩（一个世界一个状态）

把「世界」记成一条状态帧（type=world、context_ref=world:{id}）：世界绑几个群都只有这一条帧，
帧上记的是**入场身份**（world_role），不是"通道群"——命令直达世界程序，不经过群聊
（见 services/world/world_event_hook.deliver_command；帧规则见 docs/dev/frame_lifecycle.md）。

进哪个世界：显式 world_id > 当前群绑的世界 > 他直接绑定的世界 > 他所在群绑的世界
（前两档之外只有唯一答案时才自动选，多个就让他用 world_id 指定——私信里也进得去）。
能不能进：世界绑了这个 AI，或者绑了他所在的群——不再要求"挑一个群当通道"，
因为那是"命令要发到群里"时代的产物，世界帧本身只认世界。

设计：docs/group_world/design/world_agent_capabilities.md
"""
from sqlalchemy.ext.asyncio import AsyncSession

from app.tools.base import ToolPlugin, ToolRegistry


class _EntryDenied(Exception):
    """进不去：把话原样回给 AI（工具的错误约定是 {"error": True, "message": ...}）。"""


# "他能进哪些世界 / 在这个世界里什么身份"是同一份事实，统一在 world_frame_service
# （reachable_worlds / resolve_world_role）——工具只负责问与说，不再自己抄一遍查询。


async def _resolve_world(db, agent, chat_group_id: int | None, arguments: dict):
    """进哪个世界：显式 world_id（须真的能进）> 唯一可进的那个 > 让他指定。"""
    from app.models.world import World
    from app.services.world.world_frame_service import reachable_worlds

    raw = arguments.get("world_id")
    if raw is not None:
        try:
            world_id = int(raw)
        except (TypeError, ValueError):
            raise _EntryDenied("world_id 必须是世界编号（整数）")
        world = await db.get(World, world_id)
        if world is None:
            raise _EntryDenied(f"世界 #{world_id} 不存在")
        if world_id not in {w.id for w in await reachable_worlds(db, agent, chat_group_id)}:
            raise _EntryDenied(
                f"世界 #{world_id} 既没绑定你，也没绑定你所在的群——先在这个群玩，"
                "或让群主把世界绑给你")
        return world

    worlds = await reachable_worlds(db, agent, chat_group_id)
    if not worlds:
        raise _EntryDenied("你手上没有可进的世界：当前群没绑世界，也没有世界绑定你")
    if len(worlds) > 1:
        names = "、".join(f"#{w.id}「{w.name}」" for w in worlds)
        raise _EntryDenied(f"可进的世界不止一个（{names}），请用 world_id 指定进哪个")
    return worlds[0]


class EnterWorld(ToolPlugin):
    name = "enter_world"
    description = (
        "进入群视界（一个世界）游玩：状态切到这个世界（状态摘要里出现世界名），工具换成这个世界"
        "颁布的技能 + 少量基础工具，群里/私信里都是这段身份；出来用 pop_state。"
        "不带参数＝进当前群绑定的世界；一个世界里只要一条状态，与群无关。"
    )
    segment = "chat_social"
    parameters = {
        "world_id": {
            "type": "integer",
            "nullable": True,
            "description": "世界编号（可进的世界不止一个时指定；不填＝当前群绑定的那个）",
        },
        "why": {"type": "string", "nullable": True, "description": "进世界做什么（可选）"},
    }
    required = []
    states = ["active", "dnd", "inactive"]
    admin_description = "AI 进入世界游玩（一个世界一个状态，命令直达世界程序）"
    trigger_condition = "群绑定了世界，且 AI 要进这个世界玩时"

    async def execute(self, db: AsyncSession, agent_id: int, group_id: int | None,
                      arguments: dict, context: dict) -> dict:
        from app.models.agent import Agent

        agent = context.get("_agent") or await db.get(Agent, agent_id)
        if agent is None:
            return {"error": True, "message": "AI 不存在，无法进世界"}

        try:
            world = await _resolve_world(db, agent, group_id, arguments)
        except _EntryDenied as denied:
            return {"error": True, "message": str(denied)}

        from app.services.world.world_frame_service import (
            activate_world_frame, resolve_world_role, world_frame_tools_for,
        )

        # 身份只在进门这一刻定：AI 自己的绑定 > 这次进来的群 > 无（只拿通用技能）
        role = await resolve_world_role(db, agent, world.id, group_id)
        _, restored = await activate_world_frame(
            db, agent_id, world, world_role=role, why=str(arguments.get("why") or ""))
        # 不 commit：提交是调用者的事（work_session 出块即提交），与 world_command 一致
        # 工具集（基础集 + 这个世界颁布的技能）不写进帧里：帧上冻一份会在世界改技能后过期，
        # 白名单由 world_frame_service 现算，这里只把"现在有什么"报给他
        tools = await world_frame_tools_for(db, agent, world.id, role)

        role_note = f"，身份「{role}」" if role else ""
        return {
            "success": True,
            "world_id": world.id,
            "world_name": world.name,
            "role": role,
            "restored": restored,
            "tools": tools,
            "message": (f"已进入世界「{world.name}」（#{world.id}{role_note}）。"
                        f"在世界里你的工具只有这些：{'、'.join(tools)}；"
                        "用 world_command 或这个世界颁布的技能操作它（命令直接交给世界程序，"
                        "不需要发到群里），出来用 pop_state"),
        }


ToolRegistry.register(EnterWorld)
