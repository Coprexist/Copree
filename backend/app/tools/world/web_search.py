"""web_search — 网页搜索（世界侧）

文案与参数直接取主站工具（唯一来源），避免两侧各写一份、加参数时漏改一边。
"""

from app.tools.file_operations.web_search import WebSearch
from app.tools.world.base import WorldToolPlugin, WorldToolContext


class WebSearchTool(WorldToolPlugin):
    name = 'web_search'
    label = '网页搜索'
    segment = 'net'

    description = WebSearch.description
    parameters = WebSearch.parameters
    required = WebSearch.required

    async def execute(self, ctx: WorldToolContext) -> dict:
        # 复用主系统同一份实现（同一份代码，无 opencli 依赖）
        try:
            from app.tools.world.shared import from_site_result
            result = await WebSearch().execute(ctx.world_repo.session, 0, None, ctx.args, {})
            return from_site_result(result)          # 主站错误形状 → 世界约定（唯一适配点）
        except (ValueError, TypeError) as e:
            return {"success": False, "error": str(e)}

    def summary(self, result: dict) -> str:
        ok = bool(result.get("success"))
        if ok:
            return f"搜索结果 {result.get('count', 0)} 条：" + "、".join(r.get('title', '')[:20] for r in (result.get('results') or [])[:5])
        return f"搜索失败：{result.get('error', '未知错误')}"
