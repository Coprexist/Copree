"""note_experience — 记一笔「我在这个世界里干了什么」

与 store_memory 的分工：store_memory 是长期事实（会衰减、要检索）；经历是「去过哪、干过什么」
的指针——换到另一个会话（同一个世界的不同对话）时，那边的你能想起它，进过就不再投。

平台在轮末也会兜底记一行工具总账（见 experience/world_experience.py），这个工具用于
「这轮没调工具但值得记」的场合。
"""
import logging

from app.tools.world.base import WorldToolPlugin, WorldToolContext

logger = logging.getLogger(__name__)


class NoteExperienceTool(WorldToolPlugin):
    name = "note_experience"
    label = "记经历"
    segment = "memory"

    description = (
        "记一笔「我在这个世界里干过什么」：换到另一个会话时，那边的你能想起这一笔。\n"
        "写什么：做了什么 + 结论或下一步，一两句。不要写别人的姓名与隐私、金额、只在当前"
        "会话成立的约定——这行会被别的会话里的你看到。\n"
        "长期事实（世界设定、用户偏好）仍用 store_memory，别塞这里。"
    )

    parameters = {
        "what": {"type": "string", "description": "这一笔干了什么（一两句，含结论或下一步）"},
    }
    required = ["what"]

    async def execute(self, ctx: WorldToolContext) -> dict:
        from app.services.experience import experience_service
        from app.services.experience.world_experience import OWNER_TYPE, scene_of

        what = str((ctx.args or {}).get("what", "")).strip()
        if not what:
            return {"success": False, "error": "what 不能为空：写清这一笔干了什么"}
        try:
            from app.services.world.world_service import ensure_world_ai

            wai = await ensure_world_ai(ctx.world_repo, ctx.world.id)
            scene, label = scene_of(ctx.world)
            row = await experience_service.record(
                ctx.world_repo.session, owner_type=OWNER_TYPE, owner_id=wai.id,
                context_ref=scene, summary=what, label=label,
                source=experience_service.SOURCE_AI, frame_type="world",
            )
            if row is None:
                return {"success": False, "error": "没写成（摘要为空或落库失败）"}
            return {"success": True, "id": row["id"]}
        except Exception as e:
            logger.warning(f"🌐 世界 #{ctx.world.id} 记经历失败: {e}")
            return {"success": False, "error": str(e)[:200]}

    def summary(self, result: dict) -> str:
        return "已记下这一笔经历" if result.get("success") else f"记经历失败：{result.get('error', '未知错误')}"
