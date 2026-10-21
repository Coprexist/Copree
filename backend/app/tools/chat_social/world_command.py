"""
world_command 工具 — 群 AI 操作绑定世界（命令直接交给世界程序）

执行 = 把一条事件喂给世界程序的 handle(event)（**不经过群聊**，见
services/world/world_event_hook.deliver_command）：
- 非常驻世界同步拿回 handle() 的返回值，那就是世界的回话；
- 常驻世界是流式的（进程内队列），只能确认送达，结果由世界自己发布。

目标世界 = 他现在所在的世界（世界帧）。不在世界里就不猜：让他先 enter_world——
"命令该发到哪个群"曾经是这个工具最麻烦的一档逻辑（显式群 → 最近的群 → 帧上的通道群），
那种麻烦是因为命令被实现成了"以 AI 名义发一条群消息"；帧直连世界之后它整体消失了。

设计：docs/group_world/design/world_agent_capabilities.md（路径 B）
"""
from sqlalchemy.ext.asyncio import AsyncSession

from app.tools.base import ToolPlugin


class WorldCommand(ToolPlugin):
    name = "world_command"
    description = (
        "向世界发送命令（操作世界：移动角色/触发事件/查询状态等，语法由世界程序定义）。"
        "命令直接交给世界程序、不经过群聊，世界程序的返回值就是它的回话。"
        "先 enter_world 进世界，再发命令。具体命令清单见系统提示末尾的【本群世界】。"
    )
    segment = "chat_social"
    parameters = {
        "command": {
            "type": "string",
            "description": "发送给世界程序的命令文本，如「旅人移动到 2,3」/「我去 2,3」/「身份 签到」",
        },
    }
    required = ["command"]
    states = ["active", "dnd", "inactive"]
    admin_description = "群 AI 通过命令操作世界（与用户共用世界程序语法）"
    trigger_condition = "AI 在世界里，且需要操作世界时"

    async def execute(self, db: AsyncSession, agent_id: int, group_id: int | None,
                      arguments: dict, context: dict) -> dict:
        command = str(arguments.get("command", "")).strip()
        if not command:
            return {"error": True, "message": "command 不能为空"}

        from app.services.world.world_frame_service import active_world

        world_id, _role = await active_world(db, agent_id)
        if world_id is None:
            return {"error": True, "message":
                    "你不在世界里：先 enter_world 进世界，再发命令（可进的世界不止一个时用 world_id 指定）"}

        from app.services.world.world_event_hook import deliver_command

        outcome = await deliver_command(world_id, agent_id, command)
        if not outcome.get("success"):
            return {"error": True, "message":
                    f"世界程序没接住这条命令：{outcome.get('error') or '未知原因'}"}
        if outcome.get("mode") == "resident":
            return {
                "success": True,
                "world_id": world_id,
                "command": command,
                "resident": True,
                "message": "命令已送达（这个世界的程序常驻在跑，结果是它自己发布的，不会回到这里）",
            }
        result = outcome.get("result")
        return {
            "success": True,
            "world_id": world_id,
            "command": command,
            "world_result": result,
            "message": ("世界程序执行完毕，world_result 是它的返回值；空值表示它没有回话"
                        if result else "世界程序执行完毕，但它没有返回内容"),
        }
