"""
web_fetch 工具 — AI 通过 HTTP 请求获取网页内容（轻量，不依赖 Chromium）
"""
import asyncio
import re
import logging
import httpx
from dataclasses import dataclass
from urllib.parse import urlparse
from sqlalchemy.ext.asyncio import AsyncSession
from app.tools.base import ToolPlugin, ToolRegistry, ToolErrorCode
from app.tools.file_operations.mirror_table import mirrors_for

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# 出站取数（SSRF 防护）
# ═══════════════════════════════════════════════════════════════
# 挑地址、把选中的 IP 钉进连接、Host 头与 SNI 仍用原域名、每一跳重定向重新复检——
# 这套只写在 app/utils/outbound_http.py 一处（供应商探针走同一扇门）。这里把名字转出来，
# 调用方沿用 web_fetch.BlockedFetch / resolve_candidates / pin_url 即可，不必改 import。
#
# **官方失败自动轮播镜像**（表见 mirror_table.py）：官方永远第一优先，
# 只有官方失败才按表里的顺序试镜像；镜像走同一条取数管线（一样过 SSRF 复检），
# 用了哪个镜像必须在结果里说清楚（Fetched.mirror → 工具结果的 via）。
from app.utils.outbound_http import (  # noqa: F401 —— 兼容入口
    BlockedFetch,
    pin_url,
    request_pinned,
    resolve_candidates,
)

REDIRECT_LIMIT = 5


@dataclass(frozen=True, slots=True)
class Fetched:
    """取数结果：响应 + 实际用的地址 + 镜像主机名（None = 官方原文）

    镜像不是官方：内容来自第三方，必须一路传到工具结果里（via），AI 与用户都得知道。
    """
    response: httpx.Response
    url: str
    mirror: str | None = None

    def as_result_extra(self) -> dict:
        """并进工具结果的附加字段（没走镜像就什么都不加）"""
        return {"via": self.mirror, "via_url": self.url} if self.mirror else {}


async def safe_get(client: httpx.AsyncClient, url: str, *, headers: dict | None = None,
                   max_redirects: int = REDIRECT_LIMIT, allow_mirrors: bool = True) -> Fetched:
    """SSRF 安全的 GET（对外唯一入口）：官方优先，失败才轮播镜像。

    调用方建 client 时**不要**开 follow_redirects（每一跳都要过一遍内网判定）。
    allow_mirrors=False 时只走官方（比如"我要的就是这个站的原文"）。
    """
    try:
        return Fetched(await _fetch_with_redirects(client, url, headers, max_redirects), url)
    except BlockedFetch as official_error:
        if not allow_mirrors:
            raise
        for mirror_url in mirrors_for(url):
            try:
                resp = await _fetch_with_redirects(client, mirror_url, headers, max_redirects)
            except BlockedFetch as e:
                logger.info(f"🪞 镜像不可用（{e}）：{mirror_url[:80]}")
                continue
            if resp.status_code >= 400:
                logger.info(f"🪞 镜像返回 HTTP {resp.status_code}，继续下一个：{mirror_url[:80]}")
                continue
            logger.info(f"🪞 官方失败（{official_error}），改走镜像：{mirror_url[:100]}")
            return Fetched(resp, mirror_url, mirror=urlparse(mirror_url).hostname or "")
        raise


async def _fetch_with_redirects(client: httpx.AsyncClient, url: str, headers: dict | None,
                                max_redirects: int) -> httpx.Response:
    """按复检过的地址取一次，并逐跳复检重定向——实现在 outbound_http.request_pinned。

    这里只保留"取数"这一层的说法：client 不要开 follow_redirects，重定向必须回到
    request_pinned，否则跨域跳到内网就绕过去了。
    """
    return await request_pinned(client, "GET", url, headers=headers, max_redirects=max_redirects)


# 常用 User-Agent 伪装
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
# 最大响应字节（500KB）
MAX_RESPONSE_BYTES = 500 * 1024
# 超时
TIMEOUT = 15.0


class WebFetch(ToolPlugin):
    name = "web_fetch"
    description = (
        "上网查资料：获取指定 URL 的网页内容（纯文本）。"
        "比 browser 命令更轻量快速，适合获取网页正文、API 响应、文档等。"
        "不支持需要 JavaScript 渲染的页面（如 SPA 应用）。"
        "页面加载慢/内容延迟出现时，可设置 delay_ms 先等待再抓取。"
        "**要搜索就用 web_search**（多后端：必应 / 360 / 搜狗，自动回退）；google / duckduckgo 这类站点在国内"
        "解析被污染、抓不到，别去抓它们的搜索页。"
        "GitHub 的文件（raw / release / 压缩包）直连失败时会自动改走国内镜像，"
        "此时结果里带 via 字段说明内容来自哪个镜像（第三方，留意校验）。"
    )
    segment = "file_operations"
    parameters = {
        "url": {
            "type": "string",
            "description": "要访问的完整 URL（含 https://）",
        },
        "selector": {
            "type": "string",
            "description": "可选：只提取指定标签的内容（如 'article'、'div'、'p'）。注意：只支持 HTML 标签名，不支持 CSS 类/ID 选择器",
            "nullable": True,
        },
        "delay_ms": {
            "type": "integer",
            "description": "可选：发起请求前先等待的毫秒数（0-30000，默认 0）。目标网页加载慢/内容延迟出现时设置，给服务器和页面数据生成留出时间",
            "nullable": True,
        },
    }
    required = ["url"]
    states = ["active", "dnd"]
    admin_description = "AI 通过 HTTP 获取网页内容，无需 Chromium 浏览器。无法渲染 JavaScript 页面。支持 delay_ms 等待后再抓取。"
    trigger_condition = "AI 需要查阅网络资料时"

    async def execute(self, db: AsyncSession, agent_id: int, group_id: int | None,
                      arguments: dict, context: dict) -> dict:
        from app.utils.error_handler import build_tool_error

        url = arguments["url"].strip()
        selector = arguments.get("selector", "").strip() or None
        # AI 可设定延迟：等待指定毫秒后再发起请求（慢速/动态加载页面用）
        try:
            delay_ms = min(max(int(arguments.get("delay_ms") or 0), 0), 30000)
        except (TypeError, ValueError):
            delay_ms = 0
        if delay_ms > 0:
            await asyncio.sleep(delay_ms / 1000)

        # 基本 URL 校验
        if not url.startswith(("http://", "https://")):
            return build_tool_error(ToolErrorCode.TOOL_EXEC_FAILED, "URL 必须以 http:// 或 https:// 开头")

        # SSRF 防护 + 镜像轮播：挑地址、钉连接、逐跳复检、官方失败才走镜像，全在 safe_get 一处
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                fetched = await safe_get(client, url, headers={"User-Agent": UA})
            resp = fetched.response

            if resp.status_code >= 400:
                return {
                    "success": False,
                    "error": f"HTTP {resp.status_code}",
                    "url": url,
                    **fetched.as_result_extra(),
                }

            # 截断过大响应
            content = resp.text[:MAX_RESPONSE_BYTES]
            if len(resp.text) > MAX_RESPONSE_BYTES:
                content += "\n\n[内容已被截断，仅显示前 500KB]"

            # 简单 HTML 转文本（如果响应是 HTML）
            content_type = resp.headers.get("content-type", "")
            if "html" in content_type.lower():
                content = _html_to_text(content, selector)

            return {
                "success": True,
                "url": url,
                "status": resp.status_code,
                "content": content[:10000],  # 给 AI 看的最终内容限制 10K 字符
                "content_type": content_type,
                **fetched.as_result_extra(),   # 走了镜像要说清楚（内容来自第三方）
            }

        except BlockedFetch as e:
            return build_tool_error(ToolErrorCode.TOOL_EXEC_FAILED, str(e))
        except httpx.TimeoutException:
            return build_tool_error(ToolErrorCode.TOOL_EXEC_FAILED, f"请求超时（{TIMEOUT}s）")
        except httpx.ConnectError:
            return build_tool_error(ToolErrorCode.TOOL_EXEC_FAILED, f"连接失败：无法访问 {url}")
        except Exception as e:
            logger.error(f"web_fetch 失败: {e}", exc_info=True)
            return build_tool_error(ToolErrorCode.TOOL_EXEC_FAILED, f"请求失败: {str(e)}")


def _html_to_text(html: str, selector: str | None = None) -> str:
    """简单 HTML → 纯文本（不依赖第三方解析库，适合常见场景）"""
    # 如果提供了 selector，尝试用正则提取对应标签内容（简化版）
    if selector:
        # 匹配 <tag> 或 <tag class="..."> 或 <tag id="...">
        pattern = rf'<{selector}[^>]*>(.*?)</{selector}>'
        matches = re.findall(pattern, html, re.DOTALL)
        if matches:
            html = "\n".join(matches)

    # 去掉 script/style
    html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL)
    html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL)

    # 替换 br 为换行
    html = re.sub(r'<br\s*/?>', '\n', html, flags=re.IGNORECASE)

    # 去掉所有标签
    html = re.sub(r'<[^>]+>', '', html)

    # 解码 HTML 实体
    html = html.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    html = html.replace('&quot;', '"').replace('&#39;', "'").replace('&nbsp;', ' ')

    # 合并空白行
    lines = [line.strip() for line in html.split('\n')]
    lines = [line for line in lines if line]
    return '\n'.join(lines)


ToolRegistry.register(WebFetch)
