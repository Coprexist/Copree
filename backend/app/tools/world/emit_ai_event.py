"""emit_ai_event — 给 AI 发一条世界事件

世界程序用它主动通知 AI（补货、结算、剧情变化…）。收件人由世界决定（按 id 或按类型选群/AI）；
契约与投递语义见 docs/group_world/design/world_ai_events.md。
"""
from app.tools.world.base import WorldToolPlugin, WorldToolContext


class EmitAiEventTool(WorldToolPlugin):
    name = 'emit_ai_event'
    label = '发世界事件'
    segment = 'group'

    description = (
        '给本世界的 AI 发一条事件：它们各自按自己写的决策技能处理，没被规则处理掉的一律唤醒本体。'
        'name=事件名（小写字母数字下划线，如 restock_done，规则按它匹配）、'
        'title=给 AI 看的一句话、payload=附加字段（对象；规则条件里用 payload_字段名 引用）、'
        'targets=收件人：{"group_id": 兜底回复群, "groups": {"ids": [...], "types": ["类型slug"]}, '
        '"ais": {"ids": [...], "types": ["类型slug"]}}。targets 不传 = 发给本世界绑定的所有群里的 AI。'
    )
    parameters = {
        'name': {'type': 'string', 'description': '事件名（小写字母数字下划线，≤40 字符）'},
        'title': {'type': 'string', 'description': '给 AI 看的事件标题（≤60 字符）'},
        'payload': {'type': 'object', 'description': '可选：附加字段对象（≤4KB）'},
        'targets': {'type': 'object', 'description': '可选：收件人描述（见工具说明）'},
    }
    required = ['name', 'title']

    async def execute(self, ctx: WorldToolContext) -> dict:
        try:
            from app.services.world.world_ai_events import emit_event
            result = await emit_event(ctx.world_repo.session, ctx.world, {
                "name": ctx.args.get("name"),
                "title": ctx.args.get("title"),
                "payload": ctx.args.get("payload") or {},
                "targets": ctx.args.get("targets") or {},
            })
            if not result.get("ok"):
                return {"success": False, "error": result.get("error")}
            return {"success": True, **result}
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": str(e)}

    def summary(self, result: dict) -> str:
        if not result.get("success"):
            return f"世界事件发送失败：{result.get('error', '未知错误')}"
        return (f"世界事件「{result.get('name')}」已发出：{result.get('targets')} 个收件人"
                f"（程序处理 {result.get('handled')}，唤醒 {result.get('woke')}）")
