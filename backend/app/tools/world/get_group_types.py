"""get_group_types — 查入口类型

查看本世界的入口类型：群聊类型（群以什么场景进入）与 AI 角色（AI 以什么身份入驻，由世界自己定），
含规则/绑定上限/群助手模板，以及群/AI 两侧各自的已绑定数。设计世界、回答类型或群助手相关问题前先调用它。
"""
from app.tools.world.base import WorldToolPlugin, WorldToolContext


class GetGroupTypesTool(WorldToolPlugin):
    name = 'get_group_types'
    label = '查群类型'
    segment = 'group'

    description = (
        '查看本世界的入口类型：群聊类型（群以什么场景进入）与 AI 角色（AI 以什么身份入驻），'
        '含规则/绑定上限/群助手模板，以及群/AI 两侧各自的已绑定数。设计世界、回答类型或群助手相关问题前先调用它。'
    )

    parameters = {'entity_type': {
        'type': 'string', 'enum': ['group', 'agent'],
        'description': 'group=只看群聊类型 / agent=只看 AI 角色；不传=全部（默认）',
    }}

    required = []

    async def execute(self, ctx: WorldToolContext) -> dict:
        try:
            from app.services.world.group_type_service import list_group_types
            types = await list_group_types(ctx.world_repo, ctx.world.id, entity_type=ctx.args.get("entity_type") or None)
            return {"success": True, "types": types}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def summary(self, result: dict) -> str:
        if not result.get("success"):
            return f"查询入口类型失败：{result.get('error', '未知错误')}"
        types = result.get("types") or []
        if not types:
            return "本世界还没有入口类型（群聊类型与 AI 角色都未定义）"
        from app.services.world.group_type_service import APPLIES_LABEL
        parts = [
            f"{t['name']}（{APPLIES_LABEL.get(t.get('applies_to'), '群聊类型')}，"
            f"上限{'无限' if t.get('bind_limit') == -1 else t.get('bind_limit')}，"
            f"已绑 群{t.get('bound_groups', 0)}/AI{t.get('bound_ais', 0)}）"
            for t in types
        ]
        return "入口类型：" + "；".join(parts)
