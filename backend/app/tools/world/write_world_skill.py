"""write_world_skill — 颁布/更新一个世界侧技能

以前颁布一套技能要写两个文件（manifest.json + code.py），审阅模式下就是两次审批；
名字含连字符、权限拼错、run 忘了 async，加载器一律静默跳过，只在后端日志留一行。
这个工具把「写两个文件 + 校验 + 回读」收成一次调用，失败当场说清缺什么。
"""

from app.tools.world.base import WorldToolPlugin, WorldToolContext


class WriteWorldSkillTool(WorldToolPlugin):
    name = 'write_world_skill'
    label = '颁布世界技能'
    segment = 'world'

    description = (
        '颁布或更新一个世界侧技能（skills/<name>/ 的 manifest.json + code.py）：一次调用写全两个文件，'
        '写入前按加载器同一份规则校验（名字、权限、JSON Schema、代码语法与 import 白名单、async def run(args, ctx)），'
        '写完立即回「已注册」或具体缺什么。给 AI 居民写能力时用这个，不要手写两个文件；'
        '颁布后用 run_world_skill 试跑一次。'
    )

    parameters = {
        'name': {'type': 'string', 'description': '工具名（居民调用时看到的名字）：字母、数字、下划线' },
        'description': {'type': 'string', 'description': '给模型看的说明：什么时候该调、返回什么' },
        'arguments': {'type': 'object', 'description': '参数的 JSON Schema：{"type":"object","properties":{...},"required":[...]}' },
        'permissions': {'type': 'array', 'items': {'type': 'string'},
                        'description': '能力清单：file:list/read/write/delete、world:read/update、data:read/write、group:send' },
        'types': {'type': 'array', 'items': {'type': 'string'},
                  'description': '适用类型 slug；省略或 ["*"] = 所有类型通用' },
        'code': {'type': 'string', 'description': 'code.py 全文，必须是 async def run(args, ctx) 并返回 dict' },
    }
    required = ['name', 'description', 'code']

    async def execute(self, ctx: WorldToolContext) -> dict:
        from app.services.world.world_skill_runtime import write_world_skill

        name = str(ctx.args.get("name") or "").strip()
        await ctx.progress(f"写入技能 {name or '（未命名）'}…")
        return write_world_skill(
            ctx.world.id,
            name=name,
            description=str(ctx.args.get("description") or ""),
            code=str(ctx.args.get("code") or ""),
            arguments=ctx.args.get("arguments"),
            permissions=ctx.args.get("permissions"),
            types=ctx.args.get("types"),
        )

    def summary(self, result: dict) -> str:
        if result.get("success"):
            return f"已颁布技能 {result.get('name') or ''}"
        return f"颁布失败：{str(result.get('error') or '未知错误')[:80]}"
