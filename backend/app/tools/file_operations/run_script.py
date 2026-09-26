"""
run_script 工具 — AI 在自己的文件空间里运行 Python（沙箱语义见 docs/dev/code_sandbox.md）
"""
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from app.tools.base import ToolPlugin, ToolRegistry

logger = logging.getLogger(__name__)


class RunScript(ToolPlugin):
    name = "run_script"
    description = ("在你自己的文件空间里运行一段 Python 代码（沙箱隔离：只能读写你的文件空间，"
                   "不能联网、不能起子进程，有内存与超时上限）。print 出来的内容作为 stdout 返回给你。"
                   "适合批量整理自己的文件、按规则算内容、重复计算；不适合联网取数据或长耗时任务。")
    segment = "file_operations"
    parameters = {
        "code": {"type": "string", "description": "要执行的 Python 代码"},
        "path": {"type": "string", "description": "可选：先把代码存成脚本文件（沙箱目录内的相对路径，如 scripts/daily.py）再执行"},
    }
    required = ["code"]
    states = ["active", "dnd"]
    admin_description = "在 AI 自己的文件空间里运行 Python 脚本（隔离沙箱：目录锁死、禁网络、限内存与超时）。AI 处理数据、批量整理文件时调用。"
    trigger_condition = "AI 需要用脚本处理自己的文件或数据时"

    async def execute(self, db: AsyncSession, agent_id: int, group_id: int | None,
                      arguments: dict, context: dict) -> dict:
        code = arguments.get("code") or ""
        if not isinstance(code, str) or not code.strip():
            return {"success": False, "error": "code 不能为空"}
        path = str(arguments.get("path") or "").strip()
        try:
            from app.services.sandbox.agent_sandbox import run_agent_code, script_path
            if path:
                # 脚本落到沙箱目录里再执行：存的地方和跑的地方必须是同一处，
                # 否则「脚本库」和沙箱各指一个目录，AI 自己也会搞混
                script_path(agent_id, path).write_text(code, encoding="utf-8")
                return await run_agent_code(agent_id, entry=path)
            return await run_agent_code(agent_id, code=code)
        except ValueError as e:
            return {"error": True, "message": str(e)}
        except Exception as e:
            logger.error(f"run_script 失败 (agent={agent_id}): {e}", exc_info=True)
            return {"error": True, "message": f"执行失败：{e}"}


ToolRegistry.register(RunScript)
