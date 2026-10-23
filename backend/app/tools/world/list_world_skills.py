"""list_world_skills — 查本世界已颁布的技能

与 list_world_blocks 同侧：给造物主一个「我到底颁布了什么」的入口。
被跳过的目录带原因（名字非法 / 缺 code.py / manifest 坏了 / 没放进子目录），
否则「居民为什么没有这个工具」只能翻后端日志。
"""

from app.tools.world.base import WorldToolPlugin, WorldToolContext


class ListWorldSkillsTool(WorldToolPlugin):
    name = 'list_world_skills'
    label = '查世界技能'
    segment = 'world'

    description = (
        '列出本世界已颁布给 AI 居民的世界侧技能（工具名、说明、参数名、类型、权限），'
        '以及 skills/ 下没被加载的目录与原因。排查「居民为什么没有这个工具」先看这里。'
    )

    parameters = {}
    required = []

    async def execute(self, ctx: WorldToolContext) -> dict:
        from app.services.world.world_skill_runtime import list_world_skills_detail

        return list_world_skills_detail(ctx.world.id)

    def summary(self, result: dict) -> str:
        loaded = len(result.get("skills") or [])
        skipped = len(result.get("skipped") or [])
        text = f"已加载 {loaded} 个技能"
        return text + (f"，{skipped} 个目录被跳过" if skipped else "")
