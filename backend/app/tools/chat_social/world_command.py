"""
world_command 工具 — 群 AI 操作绑定世界（命令统一交给世界程序解析）

执行 = 以 AI 身份发群消息（source="user" 触发群消息钩子）→ 世界程序 handle() → SSE 生效，
群里可见可审计，与用户共用同一套语法；工具定义与具体世界无关，命令清单走尾部动态块。

目标群依次认：显式 group_id → 候选群里第一个绑了世界的（候选按新鲜度，本次回复所在的群在前）
→ 世界帧上记的通道群。都没有就不猜，如实说「你手上没有绑了世界的群」——**不报**「群没绑世界」：
那会把"你在私信里"说成群的问题（线上误导过一次）。

设计：docs/group_world/design/world_agent_capabilities.md（路径 B）
"""
from sqlalchemy.ext.asyncio import AsyncSession

from app.tools.base import ToolPlugin


class WorldCommand(ToolPlugin):
    name = "world_command"
    description = (
        "向世界发送命令（操作世界：移动角色/触发事件/查询状态等，语法由世界程序定义）。"
        "命令以你的名义发到群里——在世界里玩（先 enter_world）时省略 group_id，它就知道发去哪；"
        "在私信里还没进世界时，要带上目标群的 group_id。具体命令清单见系统提示末尾的【本群世界】。"
    )
    segment = "chat_social"
    parameters = {
        "command": {
            "type": "string",
            "description": "发送给世界程序的命令文本，如「旅人移动到 2,3」/「我去 2,3」/「身份 签到」",
        },
        "group_id": {
            "type": "integer",
            "nullable": True,
            "description": "命令发到哪个群（进世界后不用填；群聊里省略 = 当前群）",
        },
    }
    required = ["command"]
    states = ["active", "dnd", "inactive"]
    admin_description = "群 AI 通过命令操作本群绑定的世界（与用户共用世界程序语法）"
    trigger_condition = "本群绑定了世界，且需要操作世界时"

    async def execute(self, db: AsyncSession, agent_id: int, group_id: int | None,
                      arguments: dict, context: dict) -> dict:
        command = str(arguments.get("command", "")).strip()
        if not command:
            return {"error": True, "message": "command 不能为空"}

        from app.services.agent.state_stack_service import current_context
        from app.services.world.world_service import find_worlds_by_entity, first_world_bound_group

        explicit = arguments.get("group_id")
        if explicit is not None:
            try:
                target_group = int(explicit)
            except (TypeError, ValueError):
                return {"error": True, "message": "group_id 必须是群编号（整数）"}
            worlds = await find_worlds_by_entity(db, "group", target_group)
            if not worlds:
                return {"error": True, "message": f"群 {target_group} 未绑定世界（可在世界列表给群配置世界后使用）"}
        else:
            ctx = await current_context(db, agent_id, group_id)
            target_group, worlds = await first_world_bound_group(db, ctx["group_ids"])
            if not worlds and ctx["channel_group_id"]:
                target_group = int(ctx["channel_group_id"])
                worlds = await find_worlds_by_entity(db, "group", target_group)
            if not worlds:
                return {"error": True, "message":
                        "不知道这条命令该发到哪个群：你手上没有绑了世界的群——"
                        "在私信里就先 enter_group(群号) 或 enter_world 进世界，也可以直接带 group_id"}

        # 以 AI 身份发群消息 → 群消息钩子（source="user"）→ 世界程序 handle 解析执行
        from app.chat.gm import send_gm_message
        await send_gm_message(db, target_group, "ai", agent_id, command, source="user")

        return {
            "success": True,
            "message": f"命令已交给世界程序处理（你以群里可见的方式发布了：{command}）",
            "world_id": worlds[0].id,
        }
