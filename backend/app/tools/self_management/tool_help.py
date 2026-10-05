"""
tool_help 工具 — AI 查询工具或 CLI 命令的详细用法
"""
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from app.tools.base import ToolPlugin, ToolRegistry, SKILL_SEGMENT_META

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# CLI 命令详细知识库（OpenCLI 子命令的完整使用协议）
# ═══════════════════════════════════════════════════════════════

CLI_KNOWLEDGE: dict[str, dict] = {
    "browser": {
        "title": "opencli browser — 浏览器操作",
        "summary": "通过 Chrome 浏览器上网查资料、访问网页、提取内容。",
        "syntax": "execute_command(command='browser', args=['<session>', '<子命令>', ...])",
        "session": (
            "**session（会话名）**是随便取的名字（如 work、research、web1），"
            "多次调用时保持一致即可在同一标签页继续操作。**不需要初始化**，直接用。"
            "不同 session 互相隔离。"
        ),
        "subcommands": {
            "open <url>": "打开网页",
            "state": "查看页面状态（URL、标题、可点击元素及 [N] 编号）",
            "click <N>": "点击编号为 N 的元素（编号来自 state）",
            "type <N> <text>": "点击输入框 N，然后输入文本",
            "fill <N> <text>": "精确填充输入框（不动鼠标）",
            "scroll <direction>": "滚动页面（up/down/top/bottom）",
            "extract": "提取页面内容为 Markdown",
            "screenshot [path]": "截取当前页面",
            "find <selector>": "用 CSS 选择器查找元素",
            "select <N> <option>": "选择下拉菜单选项",
            "wait time <秒>": "等待 N 秒让页面加载",
            "wait selector <css>": "等待指定元素出现",
            "eval <js>": "在页面中执行 JavaScript",
            "keys <key>": "发送按键（Enter/Escape/PageDown 等）",
            "back": "浏览器后退",
            "hover <N>": "鼠标悬停在元素上",
            "network": "捕获网络请求",
            "close": "释放标签页租约",
        },
        "workflow": (
            "**典型查资料流程：**\n"
            "1. open <url> → 打开目标网页\n"
            "2. state → 查看页面有哪些可交互元素\n"
            "3. click <N> / scroll → 导航到目标区域\n"
            "4. extract → 提取内容阅读\n"
            "5. wait time <秒> → 页面加载慢时等待"
        ),
    },
    "gh": {
        "title": "opencli gh — GitHub CLI",
        "summary": "搜索仓库、查看代码、管理 Issue/PR。",
        "syntax": "execute_command(command='gh', args=['<子命令>', ...])",
        "note": "子命令列表可用 execute_command(command='gh', args=['--help']) 查看。",
    },
    "docker": {
        "title": "opencli docker — Docker 操作",
        "summary": "容器和镜像管理。",
        "syntax": "execute_command(command='docker', args=['<子命令>', ...])",
        "note": "子命令列表可用 execute_command(command='docker', args=['--help']) 查看。",
    },
    "obsidian": {
        "title": "opencli obsidian — Obsidian 笔记",
        "summary": "读写 Obsidian 知识库。",
        "syntax": "execute_command(command='obsidian', args=['<子命令>', ...])",
        "note": "子命令列表可用 execute_command(command='obsidian', args=['--help']) 查看。",
    },
    "tg": {
        "title": "opencli tg — Telegram CLI",
        "summary": "Telegram 消息收发。",
        "syntax": "execute_command(command='tg', args=['<子命令>', ...])",
        "note": "子命令列表可用 execute_command(command='tg', args=['--help']) 查看。",
    },
    "wx": {
        "title": "opencli wx — 微信 CLI",
        "summary": "微信消息收发。",
        "syntax": "execute_command(command='wx', args=['<子命令>', ...])",
        "note": "子命令列表可用 execute_command(command='wx', args=['--help']) 查看。",
    },
}


# ═══════════════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════════════

class ToolHelp(ToolPlugin):
    name = "tool_help"
    description = "查询工具或CLI命令的详细用法与参数说明。不知道怎么用时先查这个。"
    segment = "self_management"
    parameters = {
        "query": {
            "type": "string",
            "description": "要查的工具名或CLI命令名，如 browser、send_message、execute_command",
        },
    }
    required = ["query"]
    states = ["active", "dnd"]
    admin_description = "AI 的工具/CLI 用法速查手册，包含 browser 等 OpenCLI 命令的完整使用协议。"
    trigger_condition = "AI 不确定工具用法时"

    async def execute(self, db: AsyncSession, agent_id: int, group_id: int | None,
                      arguments: dict, context: dict) -> dict:
        query = arguments["query"].strip().lower()

        # ── 1. CLI 知识库精确匹配 ──
        if query in CLI_KNOWLEDGE:
            info = CLI_KNOWLEDGE[query]
            lines = [f"## {info['title']}", "", info["summary"], "", f"**调用方式：** `{info['syntax']}`"]

            if "session" in info:
                lines.extend(["", info["session"]])

            if "subcommands" in info:
                lines.extend(["", "### 子命令", ""])
                for cmd, desc in info["subcommands"].items():
                    lines.append(f"- **{cmd}** — {desc}")

            if "workflow" in info:
                lines.extend(["", "### 使用流程", "", info["workflow"]])

            if "note" in info:
                lines.extend(["", info["note"]])

            return {
                "success": True,
                "query": query,
                "type": "cli_command",
                "help_text": "\n".join(lines),
            }

        # ── 2. 注册工具精确匹配 ──
        plugin = ToolRegistry.get_plugin(query)
        if plugin:
            # 没归段的工具（如决策技能四件套）标题里不要留个空的「— 段」
            seg = SKILL_SEGMENT_META.get(plugin.segment, {}).get("name") or plugin.segment
            lines = [
                f"## {plugin.name} — {seg}段" if seg else f"## {plugin.name}",
                "",
                plugin.description,
                "",
                "### 参数",
            ]
            for pname, pinfo in plugin.parameters.items():
                req_mark = "（必填）" if pname in plugin.required else "（可选）"
                null_mark = "，可为空" if pname in plugin.nullable else ""
                lines.append(f"- **{pname}** {req_mark}：{pinfo.get('description', '')}{null_mark}")

            if plugin.required:
                lines.append(f"\n必填参数：{' / '.join(plugin.required)}")
            lines.append(f"\n可用状态：{' / '.join(plugin.states)}")

            return {
                "success": True,
                "query": query,
                "type": "tool",
                "help_text": "\n".join(lines),
            }

        # ── 3. 模糊搜索 ──
        matches_cli = [k for k in CLI_KNOWLEDGE if query in k]
        matches_tool = [
            name for name in ToolRegistry.get_all_plugins()
            if query in name or query in name.replace("_", " ")
        ]

        # ── 4. 未找到 ──
        if not matches_cli and not matches_tool:
            all_cli = "、".join(sorted(CLI_KNOWLEDGE.keys()))
            all_tools = "、".join(sorted(ToolRegistry.get_all_plugins().keys()))
            return {
                "success": True,
                "query": query,
                "type": "not_found",
                "help_text": (
                    f"未找到「{query}」的用法。\n\n"
                    f"**可查询的 CLI 命令：**{all_cli}\n\n"
                    f"**可查询的工具：**{all_tools}\n\n"
                    f"提示：CLI 命令也可以用 execute_command(command='<命令>', args=['--help']) 直接查。"
                ),
            }

        # ── 5. 返回模糊匹配列表 ──
        lines = [f"「{query}」未精确匹配，你可能要找的是："]
        if matches_cli:
            lines.append(f"\n**CLI 命令：**{' / '.join(matches_cli)}")
        if matches_tool:
            lines.append(f"\n**工具：**{' / '.join(matches_tool)}")
        lines.append("\n请用精确名称重新查询。")

        return {
            "success": True,
            "query": query,
            "type": "suggestions",
            "help_text": "\n".join(lines),
        }


ToolRegistry.register(ToolHelp)
