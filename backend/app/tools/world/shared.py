"""世界工具共用件。

被多个工具复用的东西放这里（参数解析、群 id 解析、下载落点与审核），
工具文件只 import 自己用得上的那几个。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid

from sqlalchemy import select

from app.models.world import WorldBinding

logger = logging.getLogger(__name__)

DOWNLOAD_DIR = "downloads"                      # 下载固定落点


def parse_args(arguments: str) -> dict:
    try:
        return json.loads(arguments or "{}")
    except json.JSONDecodeError:
        return {}


# 参数校验报错里回显的值上限：报错只是为了让模型看着自己发来的参数改，不是复述内容
_ARGS_ECHO_VALUE_LIMIT = 120
# 这几个字段本身就是几千 token 的大块内容（file_edit 的 old/new_string、file_write 的 content…）：
# 报错里只报长度不回显内容——模型知道自己发了什么，回显纯属烧上下文（世界 AI 2026-09-19 反馈）
_ARGS_LONG_FIELDS = {"content", "new_string", "old_string", "code", "plan", "body"}


def args_brief(args: dict) -> str:
    """实际收到参数的摘要（`键=值`，值截断；长文本字段只报长度），给参数校验报错用。"""
    if not args:
        return "无"
    parts = []
    for key, value in args.items():
        if key in _ARGS_LONG_FIELDS:
            size = len(value) if isinstance(value, str) else len(json.dumps(value, ensure_ascii=False))
            parts.append(f"{key}=（已收到，{size} 字符）")
            continue
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        text = " ".join(str(text).split())
        if len(text) > _ARGS_ECHO_VALUE_LIMIT:
            text = text[:_ARGS_ECHO_VALUE_LIMIT] + f"…（共 {len(text)} 字符）"
        parts.append(f"{key}={text}")
    return "，".join(parts)


def arg_error(reason: str, args: dict) -> dict:
    """参数校验失败的标准返回：点名哪个字段缺/非法 + 回显实际收到的参数。

    回显是让模型改自己发错的那个字段，而不是把整块内容重发一遍
    （file_edit 的 new_string、file_write 的 content 动辄几千 token）。
    """
    return {"success": False, "error": f"{reason}；实际收到：{args_brief(args)}"}


def lint_error(path: str, problem: dict) -> dict:
    """落盘前语法自检失败的标准返回：点名文件/行号/该行原文，并说明文件未写入。

    误报兜底：给一个明确的豁免口（skip_lint=true）——否则模型可能卡在一个
    "写不进去的文件"上，比让它写坏更贵。
    """
    where = f"第 {problem['line']} 行" if problem.get("line") else "未知位置"
    excerpt = f"：{(problem.get('excerpt') or '').strip()}" if problem.get("excerpt") else ""
    return {
        "success": False,
        "path": path,
        "syntax_error": True,
        "error": (f"{path} {where} 语法校验未通过（文件未写入）：{problem.get('error', '未知错误')}"
                  f"{excerpt}；修正后重试，确认是误报可加 skip_lint=true 强制写入"),
    }


def from_site_result(result: dict) -> dict:
    """主站工具结果 → 世界工具结果（**唯一适配点**）。

    两边的错误形状不一样：主站走 `build_tool_error`，是
    `{"error": True, "code", "message"}`（注意 error 是布尔）；世界约定是
    `{"success": False, "error": "文案"}`。不转换就会把布尔当文案展示——
    用户会看到「抓取失败：True」，AI 也读不到真正的原因（2026-09-16 用户反馈）。
    """
    if result.get("error"):
        return {"success": False,
                "error": str(result.get("message") or result.get("code") or "未知错误")}
    return result if "success" in result else {"success": True, **result}


async def bound_group_ids(ctx) -> list[int]:
    """世界绑定的群 id 列表"""
    rows = (await ctx.world_repo.execute(
        select(WorldBinding).where(
            WorldBinding.world_id == ctx.world.id,
            WorldBinding.entity_type == "group",
        )
    )).scalars().all()
    return [r.entity_id for r in rows]


async def resolve_group_ids(ctx, args: dict) -> list[int]:
    """群 id 解析：显式 group_id 优先；否则世界绑定的群（AI 无需知道编号，符合变量注入哲学）"""
    explicit = args.get("group_id")
    if explicit not in (None, "", 0):
        try:
            return [int(explicit)]
        except (TypeError, ValueError):
            pass
    return await bound_group_ids(ctx)


def normalize_code_url(url: str) -> str:
    """代码站链接归一：GitHub blob / gist 页 → raw 直链。

    AI 常直接贴浏览器地址（github.com/…/blob/… 是 HTML 页不是文件），转成可下载的 raw；
    只做能确定的形态转换，其余原样返回（不猜站点）。
    """
    m = re.match(r"https?://github\.com/([^/]+)/([^/]+)/blob/(.+)", url)
    if m:
        return f"https://raw.githubusercontent.com/{m.group(1)}/{m.group(2)}/{m.group(3)}"
    m = re.match(r"https?://gist\.github\.com/[^/]+/[^/]+/?$", url)
    if m:
        return url.rstrip("/") + "/raw"
    return url


def download_target(path: str) -> str:
    """下载固定落点：一律落在 downloads/ 下，AI 给的相对路径当 downloads/ 内的子路径。"""
    rel = (path or "").strip().lstrip("/")
    if rel.startswith(DOWNLOAD_DIR + "/"):
        rel = rel[len(DOWNLOAD_DIR) + 1:]
    if ".." in rel.split("/"):
        raise ValueError("非法路径: 不允许 .. 越界")
    return f"{DOWNLOAD_DIR}/{rel}" if rel else DOWNLOAD_DIR


def auto_download_path(url: str, content_type: str) -> str:
    """自动命名：优先 URL 文件名，其次按 content-type 映射扩展名（落在 downloads/）。"""
    name = url.split("?")[0].rstrip("/").split("/")[-1]
    if name and "." in name and not name.startswith("."):
        return f"{DOWNLOAD_DIR}/{name}"
    ext = ".html"
    if "image/png" in content_type:
        ext = ".png"
    elif "image/jpeg" in content_type:
        ext = ".jpg"
    elif "image/svg" in content_type:
        ext = ".svg"
    elif "image/webp" in content_type:
        ext = ".webp"
    elif "text/css" in content_type:
        ext = ".css"
    elif "javascript" in content_type:
        ext = ".js"
    elif "application/json" in content_type:
        ext = ".json"
    return f"{DOWNLOAD_DIR}/download-{uuid.uuid4().hex[:8]}{ext}"


async def web_download(world, arguments: str, approved: bool = False) -> dict:
    """下载网络文件到固定目录 downloads/（审核 + 后缀守卫 + 大小限制）。

    审批归平台门禁（world_ai_mode.gate_tool_call）统一负责，工具自己不再问：
    - approved=True（自动模式，或用户已在弹窗里同意）→ 直接下载；
    - approved=False（决策技能 / 定时 / 斜杠命令等没走门禁的旁路）→ 下载完成后弹窗问是否保留，
      没人应答按「不保留」删除。
    """
    import httpx
    from app.tools.file_operations.web_fetch import BlockedFetch, safe_get
    from app.services.world.world_ai_mode import get_mode, request_approval, with_user_note
    from app.services.world.world_moderation import audit, inspect
    from app.services.world.world_file_service import MAX_FILE_SIZE, delete_file, write_file_bytes

    args = parse_args(arguments)
    url = normalize_code_url(str(args.get("url") or "").strip())   # GitHub 页面链接 → raw 直链
    if not url.startswith(("http://", "https://")):
        return {"success": False, "error": "URL 必须以 http/https 开头"}
    # SSRF 防护在 safe_get 一处（挑地址 + 钉住连接 + 逐跳复检），这里不再自己解析一遍

    wid = world.id
    want_path = str(args.get("path") or "").strip()
    try:
        planned = download_target(want_path)          # 固定落点：downloads/…
    except ValueError as e:
        return {"success": False, "error": str(e)}
    reason = inspect(url, planned)                    # 下载前先审（URL + 目标文件名）
    if reason:
        audit(wid, url, planned, reason)
        return {"success": False, "error": reason}

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            fetched = await safe_get(client, url, headers={"User-Agent": "Mozilla/5.0 (Copree world downloader)"})
        r = fetched.response
        if r.status_code != 200:
            return {"success": False, "error": f"下载失败：HTTP {r.status_code}"}
        content = r.content
        path = planned if want_path else auto_download_path(url, r.headers.get("content-type", ""))
        reason = inspect(url, path, content)          # 下载后复审（文本正文，命中即不落盘）
        if reason:
            audit(wid, url, path, reason)
            return {"success": False, "error": reason}
        if len(content) > MAX_FILE_SIZE:
            return {"success": False, "error": f"文件过大（{len(content) // 1024}KB > {MAX_FILE_SIZE // 1024 // 1024}MB）"}
        write_file_bytes(wid, path, content)
    except BlockedFetch as e:
        return {"success": False, "error": str(e)}       # 内网/不可达：原因原样给 AI
    except ValueError as e:
        return {"success": False, "error": f"保存失败：{str(e)[:160]}"}
    except httpx.HTTPError as e:
        return {"success": False, "error": f"下载失败：{str(e)[:120]}"}
    logger.info(f"🌐 世界 #{wid} 已下载 {url[:60]} → {path}（{len(content)}B）"
                + (f"｜经镜像 {fetched.mirror}" if fetched.mirror else ""))

    if approved or get_mode(world) == "auto":
        return {"success": True, "path": path, "size": len(content), "url": url,
                **fetched.as_result_extra()}     # 走了国内镜像要说清楚（内容来自第三方）

    # 旁路下载：没经过平台门禁 → 下载完成后再问用户是否保留
    # 事后确认同样不默认保留：没人应答就删掉（on_timeout=False，安全默认）
    keep = await request_approval(
        wid, "", kind="download",
        title=f"是否保留刚下载的文件？{path}",
        detail=f"{url}\n{len(content) // 1024}KB → {path}",
        on_timeout=False,
    )
    if keep.approved:
        # 用户可能顺便写了要求（"留着，但改名叫 x.png"）——不能让这句话烂在弹窗里
        return with_user_note({"success": True, "path": path, "size": len(content), "url": url,
                               **fetched.as_result_extra()}, keep.instruction)
    try:
        delete_file(wid, path)
    except (ValueError, FileNotFoundError) as e:
        logger.warning(f"🌐 世界 #{wid} 未保留文件删除失败: {e}")
    return {"success": False, "path": path, "error": f"用户选择不保留，已删除刚下载的文件（{keep.reason}）"}
