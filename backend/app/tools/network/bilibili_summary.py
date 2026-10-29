"""
bilibili_summary 工具 — 获取B站视频 AI 总结

需要管理员在系统设置中配置 bilibili SESSDATA cookie，
未配置时工具返回错误提示。
"""
import logging
import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from app.tools.base import ToolPlugin, ToolRegistry

logger = logging.getLogger(__name__)

BILI_API = "https://api.bilibili.com/x/web-interface/view/conclusion/get"


class BilibiliSummary(ToolPlugin):
    name = "bilibili_summary"
    description = "获取B站视频的 AI 文字总结。需要管理员已配置B站登录状态（SESSDATA）。"
    segment = "file_operations"
    parameters = {
        "bvid": {
            "type": "string",
            "description": "B站视频 BV 号，例如「BV1GJ411x7G」。也支持 av 号（自动转 BV）。",
        },
    }
    required = ["bvid"]
    states = ["active", "dnd"]
    admin_description = "B站视频AI总结。需管理员在系统设置中配置B站 SESSDATA（登录cookie），否则对AI不可见。"
    trigger_condition = "用户请求总结B站视频时"

    async def execute(self, db: AsyncSession, agent_id: int, group_id: int | None,
                      arguments: dict, context: dict) -> dict:
        bvid = arguments["bvid"].strip()

        # av → BV 转换
        if bvid.startswith("av") or bvid.startswith("AV"):
            try:
                aid = bvid[2:]
                int(aid)  # 验证数字
                bvid = await self._aid_to_bvid(db, aid)
                if not bvid:
                    return {"error": True, "message": "av 号转 BV 号失败"}
            except ValueError:
                return {"error": True, "message": "av 号格式错误"}

        # 读取 SESSDATA
        sessdata = await self._get_sessdata(db)
        if not sessdata:
            return {
                "error": True,
                "message": "管理员尚未配置B站登录状态。请联系管理员在系统设置中填写 Bilibili SESSDATA。",
            }

        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                resp = await client.get(
                    BILI_API,
                    params={"bvid": bvid},
                    cookies={"SESSDATA": sessdata},
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                        "Referer": "https://www.bilibili.com",
                    },
                )
                resp.raise_for_status()
                data = resp.json()

                if data.get("code") != 0:
                    msg = data.get("message", "未知错误")
                    if "请求过于频繁" in msg:
                        return {"error": True, "message": "B站 API 请求频率限制，请稍后再试"}
                    if "权限不足" in msg or "sessdata" in msg.lower():
                        return {"error": True, "message": "B站登录状态失效，请联系管理员更新 SESSDATA"}
                    return {"error": True, "message": f"B站 API 返回错误: {msg}"}

                result = data.get("data", {})
                summary = result.get("conclusion", "") or result.get("summary", "") or ""
                if not summary:
                    # 尝试其他字段
                    model_result = result.get("model_result", {})
                    if model_result:
                        summary = model_result.get("output", "") or model_result.get("result", "") or ""

                if not summary:
                    return {
                        "success": True,
                        "has_summary": False,
                        "message": "该视频暂无 AI 总结",
                    }

                return {
                    "success": True,
                    "has_summary": True,
                    "bvid": bvid,
                    "summary": summary,
                }

        except httpx.TimeoutException:
            return {"error": True, "message": "请求超时，请稍后再试"}
        except Exception as e:
            logger.error(f"bilibili_summary 失败: {e}", exc_info=True)
            return {"error": True, "message": f"获取B站总结失败: {str(e)}"}

    async def _get_sessdata(self, db: AsyncSession) -> str | None:
        """从 system_settings.system_prompt_overrides 里取 bilibili.sessdata。

        用 SQLAlchemy 的 JSON 下标而不是手写 `->'bilibili'->>'sessdata'`：
        PG 编译成 ->>，SQLite 编译成 json_extract。
        """
        try:
            from sqlalchemy import select
            from app.models.system_settings import SystemSettings

            result = await db.execute(
                select(
                    SystemSettings.system_prompt_overrides["bilibili"]["sessdata"].as_string()
                ).where(SystemSettings.id == 1)
            )
            return result.scalar_one_or_none()
        except Exception:
            return None

    async def _aid_to_bvid(self, db: AsyncSession, aid: str) -> str | None:
        """av 号转 BV 号"""
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
                resp = await client.get(
                    "https://api.bilibili.com/x/web-interface/view",
                    params={"aid": aid},
                )
                data = resp.json()
                if data.get("code") == 0:
                    return data.get("data", {}).get("bvid")
        except Exception:
            pass
        return None


ToolRegistry.register(BilibiliSummary)
