"""run_world_skill — 试跑世界侧技能

造物主自己不持有世界侧技能（world_chat_service 的【能力边界】段）：想验证刚颁布的
skills/<name>/ 能不能跑，只能借居民那条执行路径试跑——同一个沙箱、同一套权限校验，
所以这里跑通 = 居民那边跑得通。没有这一步，「写完就颁布」等于让居民去撞报错。
"""

import json

from app.tools.world.base import WorldToolPlugin, WorldToolContext


# 试跑用的调用者身份：技能里读 ctx.caller 时不该看到真实的群成员或另一个 AI
_TRIAL_CALLER = {"sender_type": "creator", "name": "世界 AI 试跑"}


class RunWorldSkillTool(WorldToolPlugin):
    name = 'run_world_skill'
    label = '试跑世界技能'
    segment = 'world'

    description = (
        '试跑本世界颁布给 AI 居民的世界侧技能（skills/<name>/ 的 manifest.json + code.py）：'
        '像居民那样传参数执行一次，返回技能的返回值或报错。与居民调用走同一个沙箱、同一套权限校验，'
        '所以这里跑通，居民那边就跑得通。颁布或改完技能先用它试跑关键路径——不试跑就颁布，居民会一直撞报错。'
        '它真实执行：会真的读写世界数据，用测试局面跑，跑完清掉。'
    )

    parameters = {
        'name': {'type': 'string', 'description': '技能名（manifest.json 里的 name，不是目录名）'},
        'arguments': {'type': 'object', 'description': '传给技能的参数，按 manifest.arguments 写'},
    }
    required = ['name']

    async def execute(self, ctx: WorldToolContext) -> dict:
        from app.services.world.world_skill_runtime import execute_skill, list_world_skills

        name = str(ctx.args.get("name") or "").strip()
        available = sorted(s.name for s in list_world_skills(ctx.world.id))
        if not name:
            return {"success": False, "error": "缺少 name（技能名）", "available": available}
        if name not in available:
            # 目录名 ≠ 工具名是常见写错点：把已颁布的名字一并回给 AI，省一轮排查
            return {"success": False, "error": f"本世界没有颁布技能：{name}", "available": available}
        args = ctx.args.get("arguments") or {}
        if not isinstance(args, dict):
            return {"success": False, "error": "arguments 必须是对象（按 manifest.arguments 传）"}

        await ctx.progress(f"试跑技能 {name}…")
        result = await execute_skill(
            ctx.world_repo.session, ctx.world, name,
            json.dumps(args, ensure_ascii=False), scope="world", caller=_TRIAL_CALLER,
        )
        if result is None:
            return {"success": False, "error": f"技能 {name} 没被加载（检查 manifest.json 与 code.py）"}
        return result

    def summary(self, result: dict) -> str:
        if result.get("success"):
            gist = str(result.get("result") or result.get("message") or "").strip()
            return "试跑通过" + (f"：{gist[:80]}" if gist else "")
        return f"试跑失败：{str(result.get('error') or '未知错误')[:80]}"
