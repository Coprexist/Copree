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

        # 只列"这个 AI 真能用"的包：白名单默认下没勾选的包不出现，免得它写了发不出去
        packs = await catalog.packs_for_agent(db, agent_id)
        want = str(arguments.get("pack") or "").strip()
        if want:
            packs = [p for p in packs if want in (p["id"], p["name"])]
        if not packs:
            return {
                "available": [],
                "message": "当前没有可用表情包" + ("（没有叫这个名字的包）" if want else ""),
            }
        return {
            "how_to_use": "有字符的表情直接写该字符（最省事）；仅图片的表情写 :id: 短码或 [表情:名字]，"
                          "平台会把它作为一张图片发出去：站内显示为图，QQ 等通道发图片消息；"
                          "表情不必单独调工具发送，写进正文即可",
            "packs": [
                {
                    "id": pack["id"],
                    "name": pack["name"],
                    "usage": pack["usage"],
                    # 每行给的是"该往正文里写什么" + 这张图是什么（caption/keywords 供挑选）
                    "faces": [
                        " → ".join(filter(None, [
                            face["name"],
                            face["emoji"] or f":{face['id']}:",
                            (f"（{face['caption']}"
                             + (f"｜{face['keywords']}" if face.get("keywords") else "")
                             + (f"｜{face['mood']}" if face.get("mood") else "") + "）")
                            if face.get("caption") else "",
                        ]))
                        for face in pack["faces"]
                    ],
                }
                for pack in packs
            ],
        }


ToolRegistry.register(ListEmojiFaces)
