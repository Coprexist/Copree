"""
list_emoji_faces 工具 — AI 查看可用表情包与其中的表情名
"""
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.tools.base import ToolPlugin, ToolRegistry

logger = logging.getLogger(__name__)


class ListEmojiFaces(ToolPlugin):
    name = "list_emoji_faces"
    description = "查看已启用的表情包及每个表情的写法。发表情前先查：未登记的写法不会渲染，会原样显示为文字。"
    segment = "chat_social"
    parameters = {
        "pack": {"type": "string", "nullable": True, "description": "（可选）只看某个表情包，传包名或插件 id；不传则列出全部"},
    }
    required = []
    states = ["active", "dnd"]
    admin_description = "列出可用表情包与表情写法，避免 AI 编造不存在的表情名。"
    trigger_condition = "AI 想在消息里使用表情时"

    async def execute(self, db: AsyncSession, agent_id: int, group_id: int | None,
                      arguments: dict, context: dict) -> dict:
        from app.services.plugin import catalog

        packs = await catalog.enabled_emoji_packs(db)
        want = str(arguments.get("pack") or "").strip()
        if want:
            packs = [p for p in packs if want in (p["id"], p["name"])]
        if not packs:
            return {
                "available": [],
                "message": "当前没有可用表情包" + ("（没有叫这个名字的包）" if want else ""),
            }
        return {
            "how_to_use": "有字符的表情直接写该字符（优先，各通道均可显示）；仅图片的表情写 :id: 短码，"
                          "站内渲染为图，QQ 等无法接收富媒体的通道会被去掉",
            "packs": [
                {
                    "id": pack["id"],
                    "name": pack["name"],
                    "usage": pack["usage"],
                    # 每行给的是"该往正文里写什么"：字符或短码，不写名字
                    "faces": [f"{face['name']} → {face['emoji'] or ':' + face['id'] + ':'}"
                              for face in pack["faces"]],
                }
                for pack in packs
            ],
        }


ToolRegistry.register(ListEmojiFaces)
