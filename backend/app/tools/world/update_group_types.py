"""update_group_types — 改入口类型

设置本世界的入口类型：群聊类型（群以什么场景进入）与 AI 角色（AI 以什么身份入驻，世界自己定）。
传完整的 types 数组（先 get_group_types 看现状再改，全量替换，别丢没改的类型）。
"""

from app.tools.world.base import WorldToolPlugin, WorldToolContext


class UpdateGroupTypesTool(WorldToolPlugin):
    name = 'update_group_types'
    label = '改群类型'
    segment = 'group'

    description = (
        '设置本世界的入口类型（群聊类型 = 群以什么场景进入；AI 角色 = AI 以什么身份入驻）。'
        '传完整的 types 数组（先 get_group_types 看现状再改；全量替换，没改的类型也要带上）。\n'
        '每个类型字段：name=类型名、applies_to=适用对象（group=群聊类型 / ai=AI 角色 / both=两者，省略按群聊类型）、'
        'rules=世界规则（群主可见，群助手行为继承）、bind_limit=可绑定上限（-1 = 无限）、'
        'assistant_spec={count: 每群助手数量, need_api: 是否需API, default_name: 助手默认名}（只有群聊类型用得上）。\n'
        '命名：群聊类型是场所/组织（冒险团/商会/决斗公会）；AI 角色是它在世界里「是什么」，由你按剧本定——'
        '玩家、NPC、裁判、店员、陪练都可以，但要能对应一组能力（skill.types 与角色 slug 对齐）。'
        '头衔、关系、剧情绰号不是角色（「宿敌」「挚友」是关系，「首席」是头衔）。\n'
        '例：把冒险团上限改成 5 → types 里该类型 bind_limit=5；改成无限 → bind_limit=-1。'
    )

    parameters = {'types': {'type': 'array', 'items': {'type': 'object'},
                            'description': '完整入口类型列表（全量替换，保留不想改的；每项含 name/applies_to/rules/bind_limit/assistant_spec）'}}

    required = ['types']

    async def execute(self, ctx: WorldToolContext) -> dict:
        args = ctx.args
        try:
            from app.services.world.group_type_service import save_group_types_config
            types = await save_group_types_config(
                ctx.world_repo, ctx.world.id, ctx.world.owner_id, args.get("types") or [],
            )
            return {"success": True, "types": types}
        except ValueError as e:
            return {"success": False, "error": str(e)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def summary(self, result: dict) -> str:
        ok = bool(result.get("success"))
        if ok:
            return f"入口类型已更新（{len(result.get('types') or [])} 个类型）"
        return f"更新入口类型失败：{result.get('error', '未知错误')}"
