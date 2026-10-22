"""send_group_message — 发群消息

以本群群助手的身份在群聊发一条消息。用户要求你（或世界）在群里说话时调用。默认发到本世界绑定的群。

发言身份是群助手（group_assistant_service.say_in_group），不是群主也不是普通成员：
世界的话记在世界名下，才不会在群历史里变成"某个人说的"。
"""
from app.tools.world.base import WorldToolPlugin, WorldToolContext
from app.tools.world.shared import resolve_group_ids


class SendGroupMessageTool(WorldToolPlugin):
    name = 'send_group_message'
    label = '发群消息'
    segment = 'group'

    description = '以本群群助手的身份在群聊发一条消息。用户要求你（或世界）在群里说话时调用。默认发到本世界绑定的群。'

    parameters = {'group_id': {'type': 'integer', 'description': '可选：指定群聊 id（默认本世界绑定的群）'},
     'content': {'type': 'string', 'description': '消息内容'}}

    required = ['content']

    async def execute(self, ctx: WorldToolContext) -> dict:
        args = ctx.args
        try:
            gids = await resolve_group_ids(ctx, args)
            if not gids:
                return {"success": False, "error": "本世界未绑定任何群聊"}
            gid = gids[0]
            content = str(args.get("content") or "").strip()
            if not content:
                return {"success": False, "error": "消息内容不能为空"}
            from app.services.world.group_assistant_service import say_in_group

            msg = await say_in_group(ctx.world_repo.session, ctx.world, gid, content)
            return {"success": True, "message_id": msg.id}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def summary(self, result: dict) -> str:
        ok = bool(result.get("success"))
        return f"已发送群消息（#{result.get('message_id')}）" if ok else f"发送失败：{result.get('error', '未知错误')}"
